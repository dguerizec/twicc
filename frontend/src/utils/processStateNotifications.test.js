import test from 'node:test'
import assert from 'node:assert/strict'

import { getProcessStateNotificationEffects } from './processStateNotifications.js'


const options = {
    isViewingSession: false,
    userTurnBrowserEnabled: true,
    pendingRequestBrowserEnabled: true,
}


test('a missing mute key keeps every user-turn notification effect enabled', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', pending_requests: [] },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, true)
    assert.equal(effects.playUserTurnSound, true)
    assert.equal(effects.sendUserTurnBrowser, true)
})


test('mute suppresses user-turn effects but preserves pending-request effects', () => {
    const effects = getProcessStateNotificationEffects(
        {
            state: 'user_turn',
            mute_on_user_turn: true,
            pending_requests: [{ request_id: 'request-1' }],
        },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, false)
    assert.equal(effects.playUserTurnSound, false)
    assert.equal(effects.sendUserTurnBrowser, false)
    assert.equal(effects.showPendingRequestToast, true)
    assert.equal(effects.playPendingRequestSound, true)
    assert.equal(effects.sendPendingRequestBrowser, true)
})


test('mute does not suppress read tracking for a viewed session', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', mute_on_user_turn: true },
        { state: 'assistant_turn' },
        { ...options, isViewingSession: true },
    )

    assert.equal(effects.markViewed, true)
})
