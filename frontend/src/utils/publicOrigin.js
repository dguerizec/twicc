// Common public-origin parser for publicBaseUrl, shareBaseUrl, and
// peerBaseUrl. Mirrored by src/twicc/core/services/public_origin.py and covered
// by tests/fixtures/public_origin_cases.json.

const TRIM_RE = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g
const HTTP_SCHEME_RE = /^https?:\/\//i
const EXPLICIT_SCHEME_RE = /^[a-z][a-z0-9+.-]*:(?!\d)/i
const LOCAL_HOST_RE = /^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|(\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(local|test|localhost)|[^./:\s]+(?=:\d))(:\d+)?([/?#]|$)/i

function failure(error) {
    return { value: null, error, scheme: null, hostname: null, port: null }
}

function parseOrigin(value) {
    const raw = String(value ?? '').replace(TRIM_RE, '')
    if (!raw) return { raw, url: null, error: null }
    if (raw.startsWith('//')) return { raw, url: null, error: 'scheme' }

    let candidate
    if (HTTP_SCHEME_RE.test(raw)) candidate = raw
    else if (EXPLICIT_SCHEME_RE.test(raw)) return { raw, url: null, error: 'scheme' }
    else candidate = `${LOCAL_HOST_RE.test(raw) ? 'http' : 'https'}://${raw}`

    const authority = candidate.match(/^https?:\/\/([^/?#]*)/i)?.[1] || ''
    const hostPort = authority.slice(authority.lastIndexOf('@') + 1)
    const rawHostname = hostPort.startsWith('[') ? '' : hostPort.split(':')[0]
    if (/^[0-9.]+$/.test(rawHostname)) {
        const parts = rawHostname.split('.')
        const validIpv4 = parts.length === 4 && parts.every(part => {
            const number = Number(part)
            return /^\d+$/.test(part) && number <= 255 && String(number) === part
        })
        if (!validIpv4) return { raw, url: null, error: 'host' }
    }

    let url
    try {
        url = new URL(candidate)
    } catch {
        const hasPort = /^\[[^\]]+\]:.+$/.test(hostPort) || /^[^:]+:.+$/.test(hostPort)
        return { raw, url: null, error: hasPort ? 'port' : 'host' }
    }
    if (!['http:', 'https:'].includes(url.protocol)) return { raw, url: null, error: 'scheme' }
    if (!url.hostname) return { raw, url: null, error: 'host' }
    if (url.username || url.password) return { raw, url: null, error: 'credentials' }
    return { raw, url, error: null }
}

function success(url) {
    const hostname = url.hostname.startsWith('[') ? url.hostname.slice(1, -1) : url.hostname
    return {
        value: url.origin,
        error: null,
        scheme: url.protocol.slice(0, -1),
        hostname,
        port: url.port ? Number(url.port) : null,
    }
}

export function normalizePublicOrigin(value) {
    if (value != null && typeof value !== 'string') return failure('type')
    const { raw, url, error } = parseOrigin(value)
    if (!raw) return { value: '', error: null, scheme: null, hostname: null, port: null }
    if (error) return failure(error)
    if (url.pathname !== '/') return failure('path')
    if (url.search) return failure('query')
    if (url.hash) return failure('fragment')
    return success(url)
}

export function repairLegacyPublicOrigin(value) {
    if (value != null && typeof value !== 'string') return failure('type')
    const { raw, url, error } = parseOrigin(value)
    if (!raw) return { value: '', error: null, scheme: null, hostname: null, port: null }
    if (error) return failure(error)
    return success(url)
}

export function usablePublicOrigin(value) {
    return normalizePublicOrigin(value).value || ''
}
