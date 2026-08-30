/** Run one owner mutation without claiming that a rejected fetch did nothing. */
export async function mutatePeer(apiFetch, url, options) {
    try {
        const response = await apiFetch(url, options)
        let payload = null
        try { payload = await response.json() } catch { /* empty body */ }
        return { ok: response.ok, payload, unknown: false }
    } catch {
        return { ok: false, payload: null, unknown: true }
    }
}

/** Replace the local Peer list with the current owner REST snapshot. */
export async function reloadPeers(apiFetch, applyPeers) {
    try {
        const response = await apiFetch('/api/peers/')
        if (!response.ok) return false
        const payload = await response.json()
        applyPeers(payload.peers || [])
        return true
    } catch {
        return false
    }
}
