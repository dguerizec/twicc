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
 * @param {() => (number|null)} opts.getBookmarkId  Returns the *current* bookmark id (or null). Evaluated per prompt / per call — a bookmark can be created or removed while the artifact stays open (the host is not re-created on a bookmark change), and "Forever" must reflect the live state.
 * @param {object} opts.allowedHosts  The persisted allowlist `{ "scheme://host:port": { kind } }`.
 * @param {(target: {host: string, ip: string, kind: string, canRemember: boolean}) => Promise<'session'|'forever'|'deny'>} opts.showPrompt
 * @param {(url: string, kind: string) => Promise<void>} [opts.persistAllow]  Persist "allow forever" (bookmarked only).
 */
export function createBrokerHost({ documentUrl, getBookmarkId, allowedHosts, showPrompt, persistAllow }) {
    const allowed = { ...(allowedHosts || {}) }
    const canPersist = typeof persistAllow === 'function'
    const currentBookmarkId = () => (typeof getBookmarkId === 'function' ? getBookmarkId() : null)
    // The directory the artifact lives under; its own assets resolve below it.
    const ownDir = new URL('.', documentUrl).href

    // `allowed[key]` covers a host iff it still resolves to the kind it was
    // approved for (a rebind → re-prompt). Entries seeded from `allowedHosts`
    // are the persisted "Forever" grants; "This session" grants are added here
    // in-memory only and vanish when this host instance is torn down (the
    // artifact reloads) — same shape, just not persisted.
    function isAllowed(key, kind) {
        const entry = allowed[key]
        return !!(entry && entry.kind === kind)
    }

    // Consent gate. Serializes prompts (one dialog at a time, across hosts) AND
    // coalesces a burst to the *same* host into a single decision: while a host's
    // prompt is pending (or in-flight requests are awaiting it), every request to
    // that host shares the one outcome — allow or deny — instead of each raising
    // its own prompt. Cleared once settled, so a later request re-evaluates fresh
    // (an approved host hits `allowed`; a denied one re-asks).
    let gateChain = Promise.resolve()
    const pendingGate = {}
    function gate(key, target, url) {
        if (pendingGate[key]) return pendingGate[key]
        const run = gateChain.then(async () => {
            if (isAllowed(key, target.kind)) return // approved while we waited
            // Evaluated per prompt: "Forever" is offered only if there is a
            // bookmark to persist onto *right now* — un-bookmarking while the
            // artifact stays open must drop the option (and bookmarking must add
            // it) without a reload.
            const canRemember = canPersist && currentBookmarkId() != null
            const decision = await showPrompt({
                host: key, ip: target.ip, kind: target.kind, canRemember,
            })
            if (decision === 'deny') throw new Error('denied by user')
            // "Forever" persists onto the bookmark; "This session" (and any allow
            // with no bookmark to persist to) is remembered in-memory only.
            if (decision === 'forever' && canRemember) await persistAllow(url, target.kind)
            allowed[key] = { kind: target.kind }
        })
        gateChain = run.then(() => {}, () => {}) // keep the chain alive on either outcome
        const settled = run.finally(() => {
            if (pendingGate[key] === settled) delete pendingGate[key]
        })
        pendingGate[key] = settled
        return settled
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
        const pre = await callProxy({ bookmark_id: currentBookmarkId(), mode: 'preflight', request: req })
        if (pre.error) throw new Error(`blocked: ${pre.reason || pre.error}`)
        const target = pre.target // { ip, kind }
        const key = normalizeHostKey(req.url)

        // Already allowed (a persisted "Forever" or a prior "This session" grant)
        // and still the same kind → no prompt, no queue. Otherwise gate it through
        // serialized, per-host-coalesced consent (which may prompt, and throws
        // "denied by user" on refusal). A kind change (rebind) is not "allowed" →
        // it re-prompts.
        if (!isAllowed(key, target.kind)) {
            await gate(key, target, req.url)
        }

        // Same-origin runs host-direct (browser attaches your TwiCC session →
        // authenticated); cross-origin goes through the pinning server proxy.
        if (sameOrigin) return await hostDirectFetch(req)

        const res = await callProxy({
            bookmark_id: currentBookmarkId(),
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
