// Artifact network-broker HOST (design §9). The trusted side of the penpal RPC
// whose other end is the injected shim (shim.js). It owns the broker decision:
// serve the artifact's own assets locally, gate cross-origin requests behind the
// per-artifact allowlist + an honest user prompt, and call the server proxy —
// which pins the resolved IP and blocks the cloud metadata address.
//
// Framework-agnostic on purpose: both mounts (the SPA's FilePane wrapper and the
// dedicated shell page, phase 5) call `mountBrokerHost`, passing a `showPrompt`
// that renders in their own UI.

import { WindowMessenger, connect } from 'penpal'

const PROXY_URL = '/api/artifact-proxy/'

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

// The canonical allowlist key — must match the backend's normalize_host_key
// (scheme://host:port, effective port explicit, lower-cased, IPv6 bracketed).
const DEFAULT_PORTS = { 'http:': '80', 'https:': '443' }

function normalizeHostKey(url) {
    const u = new URL(url)
    const scheme = u.protocol.replace(':', '').toLowerCase()
    const port = u.port || DEFAULT_PORTS[u.protocol]
    // u.host already lower-cases and brackets IPv6; strip any explicit port to
    // re-append the effective one.
    const host = u.hostname.toLowerCase()
    const bracketed = host.includes(':') ? `[${host}]` : host
    return `${scheme}://${bracketed}:${port}`
}

async function callProxy(body) {
    const res = await fetch(PROXY_URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (res.status === 401) throw new Error('not authenticated')
    return await res.json()
}

/**
 * Build the broker host core.
 *
 * @param {object} opts
 * @param {string} opts.documentUrl  The artifact document's URL (to recognize its own same-origin assets).
 * @param {number|null} opts.bookmarkId  The bookmark id, or null for a non-bookmarked preview.
 * @param {object} opts.allowedHosts  The persisted allowlist `{ "scheme://host:port": { kind } }`.
 * @param {(target: {host: string, ip: string, kind: string, canRemember: boolean}) => Promise<'once'|'forever'|'deny'>} opts.showPrompt
 * @param {(url: string, kind: string) => Promise<void>} [opts.persistAllow]  Persist "allow forever" (bookmarked only).
 */
export function createBrokerHost({ documentUrl, bookmarkId, allowedHosts, showPrompt, persistAllow }) {
    const allowed = { ...(allowedHosts || {}) }
    const canRemember = bookmarkId != null && typeof persistAllow === 'function'
    // The directory the artifact lives under; its own assets resolve below it.
    const ownDir = new URL('.', documentUrl).href

    // Serialize prompts: concurrent fetches to different hosts queue one at a time.
    let promptChain = Promise.resolve()
    function queuedPrompt(target) {
        const run = promptChain.then(() => showPrompt(target))
        // Keep the chain alive regardless of this prompt's outcome.
        promptChain = run.then(() => {}, () => {})
        return run
    }

    async function hostDirectFetch(req) {
        // Run the request in the host's own (SPA) browser context, not through
        // the server proxy. For same-origin targets this is what lets the
        // browser attach your TwiCC session cookie (authenticated) — exactly as
        // for the artifact's own assets. Headers are forwarded unchanged. §6.6.
        const init = { method: req.method, headers: req.headers }
        if (req.body_base64) init.body = base64ToBytes(req.body_base64)
        const resp = await fetch(req.url, init)
        const buf = new Uint8Array(await resp.arrayBuffer())
        return {
            status: resp.status,
            reason: resp.statusText,
            headers: Object.fromEntries(resp.headers.entries()),
            body_base64: buf.length ? bytesToBase64(buf) : undefined,
        }
    }

    async function proxyFetch(req) {
        const url = new URL(req.url)
        const sameOrigin = url.origin === location.origin

        // The artifact's own files → served directly, no prompt (§6.6).
        if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)

        // Everything else is brokered the same way — cross-origin AND any other
        // same-origin target (e.g. TwiCC's own API). No target is special-cased:
        // only the cloud metadata address is ever blocked; everything else is
        // reachable with the user's per-host consent. Resolve the true target
        // first (honest prompt + pin for the cross-origin proxy).
        const pre = await callProxy({ bookmark_id: bookmarkId, mode: 'preflight', request: req })
        if (pre.error) throw new Error(`blocked: ${pre.reason || pre.error}`)
        const target = pre.target // { ip, kind }
        const key = normalizeHostKey(req.url)
        const approved = allowed[key]

        // Pre-approved AND still resolves to the same kind → no prompt. A kind
        // change (rebind) falls through to a fresh prompt.
        if (!(approved && approved.kind === target.kind)) {
            const decision = await queuedPrompt({
                host: key, ip: target.ip, kind: target.kind, canRemember,
            })
            if (decision === 'deny') throw new Error('denied by user')
            if (decision === 'forever' && canRemember) {
                await persistAllow(req.url, target.kind)
                allowed[key] = { kind: target.kind }
            }
        }

        // Same-origin runs host-direct (browser attaches your TwiCC session →
        // authenticated); cross-origin goes through the pinning server proxy.
        if (sameOrigin) return await hostDirectFetch(req)

        const res = await callProxy({
            bookmark_id: bookmarkId,
            mode: 'fetch',
            grant: 'once',
            pinned_ip: target.ip,
            request: req,
        })
        if (res.error) throw new Error(`broker: ${res.reason || res.error}`)
        return res
    }

    return { proxyFetch }
}

/**
 * Wire the broker host onto an artifact iframe over penpal. Returns the penpal
 * connection (call `.destroy()` to tear down). `opts` are forwarded to
 * `createBrokerHost`.
 */
export function mountBrokerHost(iframe, opts) {
    const host = createBrokerHost(opts)
    const messenger = new WindowMessenger({
        remoteWindow: iframe.contentWindow,
        allowedOrigins: ['*'], // same-origin in practice; the window target is the real bound
    })
    return connect({ messenger, methods: { proxyFetch: (req) => host.proxyFetch(req) } })
}
