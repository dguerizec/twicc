import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'

import { usePeersStore } from './peers.js'

test('excludes revoked messages from the pending inbox count', () => {
    setActivePinia(createPinia())
    const store = usePeersStore()
    store.applyPeers([
        { id: 'active', state: 'active' },
        { id: 'revoked', state: 'revoked' },
    ])
    store.applyMessages([
        { id: 1, peer_id: 'active', direction: 'in', status: 'pending' },
        { id: 2, peer_id: 'revoked', direction: 'in', status: 'pending' },
    ])

    assert.deepEqual(store.pendingInboundMessages.map(message => message.id), [1])
    assert.equal(store.inboxCount, 1)
})

test('counts an incoming reconnect as a pending request', () => {
    setActivePinia(createPinia())
    const store = usePeersStore()
    store.applyPeers([
        { id: 'revoked', state: 'revoked', reconnect_direction: 'received' },
    ])

    assert.deepEqual(store.pendingRequests.map(peer => peer.id), ['revoked'])
    assert.equal(store.inboxCount, 1)
})
