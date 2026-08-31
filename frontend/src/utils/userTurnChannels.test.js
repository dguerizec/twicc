import test from 'node:test'
import assert from 'node:assert/strict'

import { hasAnyUserTurnChannel } from './userTurnChannels.js'


const silent = {
    notifUserTurnToast: false,
    notifUserTurnSound: 'none',
    notifUserTurnBrowser: false,
    externalNotificationTargets: [],
}


test('every channel off means no channel', () => {
    assert.equal(hasAnyUserTurnChannel(silent), false)
})


test('each channel alone is enough', () => {
    assert.equal(hasAnyUserTurnChannel({ ...silent, notifUserTurnToast: true }), true)
    assert.equal(hasAnyUserTurnChannel({ ...silent, notifUserTurnSound: 'chime' }), true)
    assert.equal(hasAnyUserTurnChannel({ ...silent, notifUserTurnBrowser: true }), true)
    assert.equal(hasAnyUserTurnChannel({
        ...silent,
        externalNotificationTargets: [{ enabled: true, notifyUserTurn: true }],
    }), true)
})


test('an external target opts in when the key is absent', () => {
    assert.equal(hasAnyUserTurnChannel({
        ...silent,
        externalNotificationTargets: [{ enabled: true }],
    }), true)
})


test('a disabled or opted-out external target is not a channel', () => {
    assert.equal(hasAnyUserTurnChannel({
        ...silent,
        externalNotificationTargets: [{ enabled: false, notifyUserTurn: true }],
    }), false)
    assert.equal(hasAnyUserTurnChannel({
        ...silent,
        externalNotificationTargets: [{ enabled: true, notifyUserTurn: false }],
    }), false)
})


test('a target that only wants pending requests is not a user-turn channel', () => {
    assert.equal(hasAnyUserTurnChannel({
        ...silent,
        externalNotificationTargets: [
            { enabled: true, notifyUserTurn: false, notifyPendingRequest: true },
        ],
    }), false)
})


test('missing settings are treated as no channel', () => {
    assert.equal(hasAnyUserTurnChannel(null), false)
    assert.equal(hasAnyUserTurnChannel({}), false)
})
