import assert from 'node:assert/strict'
import test from 'node:test'

import {
    appendDraftMessageText,
    appendToDraftRecord,
    createDraftMessageSync,
    replaceDraftMessageText,
    shouldApplyDraftMessageUpdate,
} from './draftMessageSync.js'

class FakeBroadcastChannel {
    static channels = new Map()

    constructor(name) {
        this.name = name
        this.listeners = new Set()
        const peers = FakeBroadcastChannel.channels.get(name) || new Set()
        peers.add(this)
        FakeBroadcastChannel.channels.set(name, peers)
    }

    addEventListener(type, listener) {
        if (type === 'message') this.listeners.add(listener)
    }

    postMessage(data) {
        for (const peer of FakeBroadcastChannel.channels.get(this.name) || []) {
            if (peer === this) continue
            for (const listener of peer.listeners) listener({ data })
        }
    }

    close() {
        FakeBroadcastChannel.channels.get(this.name)?.delete(this)
    }
}

test('appendDraftMessageText preserves an existing composer draft', () => {
    assert.equal(
        appendDraftMessageText('  Existing draft  ', 'Peer envelope'),
        'Existing draft\n\nPeer envelope',
    )
    assert.equal(appendDraftMessageText('', 'Peer envelope'), 'Peer envelope')
})

test('appendToDraftRecord preserves persisted attachment metadata', () => {
    assert.deepEqual(
        appendToDraftRecord(
            { message: 'Persisted text', mediaIds: ['media-1', 'media-2'] },
            { message: 'Latest local text', mediaIds: ['stale-media'] },
            'Peer envelope',
        ),
        {
            message: 'Latest local text\n\nPeer envelope',
            mediaIds: ['media-1', 'media-2'],
        },
    )
})

test('mounted composers accept explicit appends without overwriting unrelated typing', () => {
    assert.equal(shouldApplyDraftMessageUpdate('', 'Peer envelope'), true)
    assert.equal(
        shouldApplyDraftMessageUpdate('Existing draft', 'Existing draft\n\nPeer envelope'),
        true,
    )
    assert.equal(
        shouldApplyDraftMessageUpdate('User is typing', 'Older persisted draft'),
        false,
    )
    assert.equal(shouldApplyDraftMessageUpdate('Same text', 'Same text'), false)
})

test('composer updates retain committed draft metadata', () => {
    assert.deepEqual(
        replaceDraftMessageText({ message: 'Old', mediaIds: ['media-1'] }, 'New'),
        { message: 'New', mediaIds: ['media-1'] },
    )
})

test('draft commits reach another TwiCC page but not the sender', () => {
    const source = createDraftMessageSync(FakeBroadcastChannel)
    const target = createDraftMessageSync(FakeBroadcastChannel)
    const sourceUpdates = []
    const targetUpdates = []
    source.subscribe(update => sourceUpdates.push(update))
    target.subscribe(update => targetUpdates.push(update))

    const draft = { message: 'Peer envelope', mediaIds: ['media-1'] }
    assert.equal(source.publish('session-1', draft), true)
    assert.deepEqual(sourceUpdates, [])
    assert.deepEqual(targetUpdates, [{ sessionId: 'session-1', draft }])

    source.close()
    target.close()
})

test('draft sync ignores malformed messages', () => {
    const source = createDraftMessageSync(FakeBroadcastChannel)
    const target = createDraftMessageSync(FakeBroadcastChannel)
    const updates = []
    target.subscribe(update => updates.push(update))

    source.publish('', { message: 'Missing session' })
    source.publish('session-1', { title: 'Missing message' })
    assert.deepEqual(updates, [])

    source.close()
    target.close()
})
