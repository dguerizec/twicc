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

// Penpal's connect() has no timeout by default. We arm the interceptor before
// the handshake completes (below), so an absent host would otherwise leave every
// queued request pending forever (a frozen page). Bound the wait: past this, the
// queued requests fail with a clear broker error instead of hanging.
const HANDSHAKE_TIMEOUT_MS = 10000

function main() {
    const messenger = new WindowMessenger({
        remoteWindow: window.parent,
        // The host validates the artifact; same-origin in practice.
        allowedOrigins: ['*'],
    })

    // Resolves with the host proxy once the handshake completes, rejects if no
    // host ever answers. Requests intercepted before then await this promise —
    // the interceptor holds them pending — and flush once it settles.
    let resolveHost, rejectHost
    const hostReady = new Promise((resolve, reject) => {
        resolveHost = resolve
        rejectHost = reject
    })
    hostReady.catch(() => {}) // no-op: avoid an unhandledrejection if no request ever fires

    if (window.parent === window) {
        // Top-level document (opened directly, without a broker wrapper) — no host
        // will ever answer, so don't even wait out the timeout.
        rejectHost(new Error('no host'))
    } else {
        connect({ messenger, timeout: HANDSHAKE_TIMEOUT_MS }).promise.then(resolveHost, rejectHost)
    }

    // Armed SYNCHRONOUSLY, before any artifact script runs (the shim is injected
    // as a blocking <script> first in <head>). A fetch/XHR fired during the
    // handshake window is captured and held here, never leaked to the native
    // fetch the CSP blocks.
    const interceptor = new BatchInterceptor({
        name: 'twicc-artifact-broker',
        interceptors: browserInterceptors,
    })
    interceptor.apply()

    interceptor.on('request', async ({ request, controller }) => {
        let host
        try {
            host = await hostReady
        } catch {
            // No host, or the handshake timed out. Fail with a clear broker error
            // — never fall through to the native fetch the CSP would block.
            controller.errorWith(new TypeError('broker: host unavailable'))
            return
        }
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
