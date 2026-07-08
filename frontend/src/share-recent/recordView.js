// Recent-shares list for the share host homepage (/share/). Isolated by origin
// (the dedicated share host), never sent anywhere. token + kind + title + lastAccess.
const KEY = 'twicc-share-recent'
const MAX = 50

export function readRecentShares() {
    try {
        const arr = JSON.parse(localStorage.getItem(KEY) || '[]')
        return Array.isArray(arr) ? arr : []
    } catch { return [] }
}

function write(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))) } catch { /* quota/opaque origin */ }
}

/** Upsert an opened share, keyed by token. `tokenPath` is like `/share/<token>`. */
export function recordShareView({ tokenPath, kind, title }) {
    const token = String(tokenPath || '').replace(/\/+$/, '').split('/').pop()
    if (!token) return
    const list = readRecentShares().filter((e) => e.token !== token)
    list.unshift({ token, kind: kind || 'session', title: title || '', lastAccess: new Date().toISOString() })
    write(list)
}

export function removeRecentShare(token) {
    write(readRecentShares().filter((e) => e.token !== token))
}
