import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    COMPANION_SOURCE,
    HOST_SOURCE,
    companionMessage,
    hostMessage,
    isCompanionMessage,
    isHostMessage,
} from './protocol.js'

test('companionMessage builds a versioned envelope with extra fields', () => {
    const msg = companionMessage('state', { url: 'http://localhost:5173/x' })
    assert.deepEqual(msg, { source: COMPANION_SOURCE, v: 1, type: 'state', url: 'http://localhost:5173/x' })
})

test('hostMessage builds a versioned envelope', () => {
    assert.deepEqual(hostMessage('ack'), { source: HOST_SOURCE, v: 1, type: 'ack' })
})

test('isCompanionMessage accepts its own envelopes and rejects everything else', () => {
    assert.equal(isCompanionMessage(companionMessage('hello')), true)
    assert.equal(isCompanionMessage(hostMessage('ack')), false)
    assert.equal(isCompanionMessage(null), false)
    assert.equal(isCompanionMessage('hello'), false)
    assert.equal(isCompanionMessage({ source: COMPANION_SOURCE, v: 2, type: 'hello' }), false)
})

test('isHostMessage accepts its own envelopes and rejects everything else', () => {
    assert.equal(isHostMessage(hostMessage('command', { action: 'back' })), true)
    assert.equal(isHostMessage(companionMessage('hello')), false)
    assert.equal(isHostMessage({}), false)
})
