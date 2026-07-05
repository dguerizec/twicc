// Address-bar input normalization for the session Browser pane. Only http(s)
// targets come out — anything else (javascript:, file:, data:, ftp:…) is
// rejected (null), never coerced. Shared by the pane's address bar and the
// project / workspace default-URL form fields.

// Hosts that get http:// (not https://) when the user types no scheme —
// local dev servers are overwhelmingly plain http. Covers localhost, IPv4,
// [::1], *.local/*.test/*.localhost, and any dotless single-label host with
// an explicit port ("devbox:9000" — a LAN/container name, never a public site).
const LOCAL_HOST_RE = /^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|(\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(local|test|localhost)|[^./:\s]+(?=:\d))(:\d+)?([/?#]|$)/i

/**
 * Normalize free-form address-bar input into an absolute http(s) URL.
 * @returns {(string|null)} the normalized URL (via `new URL().href`), or null
 *   when the input is empty, unparsable, or uses a non-http(s) scheme.
 */
export function normalizeBrowserUrl(input) {
    const raw = (input || '').trim()
    if (!raw) return null

    let candidate
    if (/^https?:\/\//i.test(raw)) {
        candidate = raw
    } else if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        return null // explicit non-http(s) scheme (file://, ftp://, …)
    } else if (/^[a-z][a-z0-9+.-]*:(?!\d)/i.test(raw)) {
        // Scheme-like prefix that is NOT a host:port (javascript:, data:,
        // mailto:…). "devbox:9000" escapes via the (?!\d) lookahead and is
        // handled as schemeless below.
        return null
    } else {
        candidate = `${LOCAL_HOST_RE.test(raw) ? 'http' : 'https'}://${raw}`
    }

    let url
    try {
        url = new URL(candidate)
    } catch {
        return null
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    if (!url.hostname) return null
    return url.href
}

// True when a normalized http(s) URL targets a local-ish host (localhost,
// bare IPs, *.local/*.test, host:port dev boxes) — used to decide whether the
// companion-script hint is actionable (it only makes sense on pages the user
// owns, not on external sites).
export function looksLocalUrl(url) {
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return false
    return LOCAL_HOST_RE.test(url.replace(/^https?:\/\//i, ''))
}
