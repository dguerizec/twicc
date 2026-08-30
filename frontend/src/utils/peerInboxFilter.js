const MESSAGE_HISTORY_LIMIT = 200
const MESSAGE_PEER_STATES = new Set(['active', 'broken', 'revoked'])

/** Whether either peer-inbox filter currently narrows message results. */
export function peerInboxFiltersActive(peerId, query) {
    return !!peerId || !!query.trim()
}

/** Partition the active inbox result source and choose its empty-state copy. */
export function peerInboxView(messages, filtersActive) {
    const received = []
    const history = []
    for (const message of messages) {
        if (message.direction === 'in' && message.status === 'pending') {
            received.push(message)
        } else {
            history.push(message)
        }
    }
    return {
        received,
        history,
        emptyMessage: received.length || history.length
            ? null
            : filtersActive ? 'No messages match your filters.' : 'No peer messages yet.',
    }
}

/** Peers that are established now, or still own retained message history. */
export function peerInboxSelectablePeers(peers, messages) {
    const peerIdsWithMessages = new Set(messages.map(message => message.peer_id))
    return peers.filter(peer =>
        MESSAGE_PEER_STATES.has(peer.state) || peerIdsWithMessages.has(peer.id)
    )
}

/** Hide revoked history until its Peer is selected explicitly. */
export function peerInboxVisibleMessages(messages, peers) {
    const revokedIds = new Set(peers.filter(peer => peer.state === 'revoked').map(peer => peer.id))
    return messages.filter(message => !revokedIds.has(message.peer_id))
}

/** Build the filtered inbox request without adding empty filter parameters. */
export function buildPeerInboxSearchUrl(peerId, query) {
    const params = new URLSearchParams({ limit: String(MESSAGE_HISTORY_LIMIT) })
    if (peerId) params.set('peer_id', peerId)
    const trimmedQuery = query.trim()
    if (trimmedQuery) params.set('q', trimmedQuery)
    return `/api/peer-messages/?${params}`
}
