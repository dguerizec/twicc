// The backend owns public-origin validation and canonicalization.
//
// The form check below is intentionally a small subset. It rejects only raw
// shapes that the Python parser also rejects. It submits every other value
// unchanged after the normative outer trim.
//
// Stored-value consumers use a separate canonical-shape guard. A stored value
// normally came from the backend. A hand-edited non-canonical value fails
// closed instead of becoming a Browser or Share URL.

const TRIM_RE = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g
const HTTP_SCHEME_RE = /^https?:\/\//i
const EXPLICIT_SCHEME_RE = /^[a-z][a-z0-9+.-]*:(?!\d)/i
const LOCAL_HOST_RE = /^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|(\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(local|test|localhost)|[^./:\s]+(?=:\d))(:\d+)?([/?#]|$)/i
const RAW_AUTHORITY_RE = /^[\x21-\x7e]+$/
const DNS_LABEL_RE = /^(?!-)(?!.*-$)[a-z0-9-]{1,63}$/
const MIXED_MAPPED_RE = /^(https?):\/\/\[::ffff:(\d+\.\d+\.\d+\.\d+)\](?::(0|[1-9]\d{0,4}))?$/

function failure(error) {
    return { value: null, error, scheme: null, hostname: null, port: null, authority: null }
}

function candidate(raw) {
    if (raw.startsWith('//')) return { error: 'scheme' }
    if (HTTP_SCHEME_RE.test(raw)) {
        return { value: raw, scheme: raw.slice(0, raw.indexOf(':')).toLowerCase() }
    }
    if (EXPLICIT_SCHEME_RE.test(raw)) return { error: 'scheme' }
    const scheme = LOCAL_HOST_RE.test(raw) ? 'http' : 'https'
    return { value: `${scheme}://${raw}`, scheme }
}

function rawAuthority(candidateValue) {
    const withoutScheme = candidateValue.replace(/^https?:\/\//i, '')
    return withoutScheme.split(/[/?#]/, 1)[0]
}

function hostnameHint(candidateValue) {
    try {
        const hostname = new URL(candidateValue).hostname
        return hostname.startsWith('[') ? hostname.slice(1, -1).toLowerCase() : hostname.toLowerCase()
    } catch {
        return null
    }
}

export function checkPublicOriginInput(value) {
    if (value != null && typeof value !== 'string') return failure('type')
    const raw = String(value ?? '').replace(TRIM_RE, '')
    if (!raw) {
        return { value: '', error: null, scheme: null, hostname: null, port: null, authority: null }
    }
    const prepared = candidate(raw)
    if (prepared.error) return failure(prepared.error)
    const authority = rawAuthority(prepared.value)
    if (!authority) return failure('host')
    if (!RAW_AUTHORITY_RE.test(authority) || authority.includes('%')) return failure('host')
    if (authority.includes('@')) return failure('credentials')
    if (authority.endsWith(':')) return failure('port')
    return {
        value: raw,
        error: null,
        scheme: prepared.scheme,
        hostname: hostnameHint(prepared.value),
        port: null,
        authority: null,
    }
}

// Temporary compatibility for the callers replaced in Task 11. Despite the
// historical name, this is the subset check above. It does not normalize.
export const normalizePublicOrigin = checkPublicOriginInput

function canonicalIpv4(value) {
    const parts = value.split('.')
    return parts.length === 4 && parts.every(part => {
        const number = Number(part)
        return /^\d+$/.test(part) && number <= 255 && String(number) === part
    })
}

function canonicalPort(scheme, token) {
    if (token == null) return true
    if (!/^(0|[1-9]\d{0,4})$/.test(token) || Number(token) > 65535) return false
    return !((scheme === 'https' && token === '443') || (scheme === 'http' && token === '80'))
}

function recognizableCanonicalStoredOrigin(value) {
    if (typeof value !== 'string' || !value || value !== value.replace(TRIM_RE, '')) return false
    if (!/^[\x21-\x7e]+$/.test(value) || value.includes('%') || !/^https?:\/\//.test(value)) return false

    const mixed = value.match(MIXED_MAPPED_RE)
    if (mixed) {
        return canonicalIpv4(mixed[2]) && canonicalPort(mixed[1], mixed[3])
    }

    let url
    try {
        url = new URL(value)
    } catch {
        return false
    }
    if (url.origin !== value || !['http:', 'https:'].includes(url.protocol)) return false

    if (url.hostname.startsWith('[')) {
        return /^\[[0-9a-f:.]+\]$/.test(url.hostname)
    }
    const hostname = url.hostname
    if (hostname === 'localhost') return true
    if (/^[0-9.]+$/.test(hostname)) return canonicalIpv4(hostname)
    return hostname.length <= 253 && hostname.split('.').every(label => DNS_LABEL_RE.test(label))
}

export function isRecognizablyCanonicalPublicOrigin(value) {
    return recognizableCanonicalStoredOrigin(value)
}

export function usablePublicOrigin(value) {
    return isRecognizablyCanonicalPublicOrigin(value) ? value : ''
}
