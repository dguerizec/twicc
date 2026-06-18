// Artifact network-broker shim (design §8). Runs first, inside an untrusted
// artifact iframe (injected as the first child of <head> by the backend). It
// transparently routes the artifact's fetch()/XHR through the trusted host
// (the iframe's parent) over a penpal RPC, so widget code and third-party libs
// use plain fetch() and never see CORS. This is DX, not the security boundary:
// the iframe's CSP `connect-src 'none'` is what actually blocks egress — the
// shim only needs to cover the happy path (fetch + XHR).

import { BatchInterceptor } from '@mswjs/interceptors'
import browserInterceptors from '@mswjs/interceptors/presets/browser'
import { WindowMessenger, connect } from 'penpal'

// Arbitrary bytes <-> base64 (btoa/atob only handle binary strings). Bodies
// cross postMessage as base64 because Request/Response aren't structured-clone.
function bytesToBase64(bytes) {
    let binary = ''
    const chunk = 0x8000
    for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
    }
    return btoa(binary)
}

function base64ToBytes(b64) {
    const binary = atob(b64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return bytes
}

async function serializeRequest(request) {
    const clone = request.clone()
    let bodyBase64
    if (request.method !== 'GET' && request.method !== 'HEAD') {
        const buf = new Uint8Array(await clone.arrayBuffer())
        if (buf.length) bodyBase64 = bytesToBase64(buf)
    }
    return {
        url: request.url,
        method: request.method,
        headers: Object.fromEntries(request.headers.entries()),
        body_base64: bodyBase64,
    }
}

function reconstructResponse(serialized) {
    const body = serialized.body_base64 ? base64ToBytes(serialized.body_base64) : null
    const headers = { ...(serialized.headers || {}) }
    // The browser recomputes content-length for the body we hand it; a stale
    // value from upstream would mismatch.
    delete headers['content-length']
    return new Response(body, {
        status: serialized.status,
        statusText: serialized.reason || '',
        headers,
    })
}

async function main() {
    const messenger = new WindowMessenger({
        remoteWindow: window.parent,
        // The host validates the artifact; same-origin in practice.
        allowedOrigins: ['*'],
    })

    let host
    try {
        host = await connect({ messenger }).promise
    } catch {
        // No host answered (e.g. the artifact opened without a broker wrapper).
        // Leave fetch/XHR untouched — the CSP blocks them, which is the correct
        // failure: a clear network error, never a silent escape.
        return
    }

    const interceptor = new BatchInterceptor({
        name: 'twicc-artifact-broker',
        interceptors: browserInterceptors,
    })
    interceptor.apply()

    interceptor.on('request', async ({ request, controller }) => {
        let serialized
        try {
            serialized = await serializeRequest(request)
        } catch (err) {
            controller.errorWith(new TypeError(`broker: bad request (${err?.message || err})`))
            return
        }
        try {
            const response = await host.proxyFetch(serialized)
            controller.respondWith(reconstructResponse(response))
        } catch (err) {
            // Denied by the user, blocked by the guard, or upstream failure.
            controller.errorWith(new TypeError(`broker: ${err?.message || 'request denied'}`))
        }
    })
}

main()
