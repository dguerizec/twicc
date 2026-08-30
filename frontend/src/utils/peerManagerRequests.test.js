import test from 'node:test'
import assert from 'node:assert/strict'

import { mutatePeer, reloadPeers } from './peerManagerRequests.js'

test('reports an unknown result when a mutation fetch rejects', async () => {
    const result = await mutatePeer(
        async () => { throw new Error('connection lost') },
        '/api/peers/peer-a/reconnect/',
        { method: 'POST' },
    )

    assert.deepEqual(result, { ok: false, payload: null, unknown: true })
})

test('reloads the authoritative Peer list without replaying a mutation', async () => {
    const calls = []
    let applied = null
    const ok = await reloadPeers(async (url, options) => {
        calls.push({ url, options })
        return {
            ok: true,
            async json() { return { peers: [{ id: 'peer-a', state: 'revoked' }] } },
        }
    }, peers => { applied = peers })

    assert.equal(ok, true)
    assert.deepEqual(calls, [{ url: '/api/peers/', options: undefined }])
    assert.deepEqual(applied, [{ id: 'peer-a', state: 'revoked' }])
})
