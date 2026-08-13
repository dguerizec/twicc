import assert from 'node:assert/strict'
import test from 'node:test'

import * as sessionComposerLock from './sessionComposerLock.js'


test('locks sending during compute preparation without overriding a pending-request reason', () => {
    assert.deepEqual(
        sessionComposerLock.resolveSessionComposerLock({
            hasAnswerablePendingRequest: false,
            isComputePending: true,
        }),
        {
            locked: true,
            reason: 'Wait for session preparation to finish before sending',
            presentation: 'disabled',
        },
    )
    assert.deepEqual(
        sessionComposerLock.resolveSessionComposerLock({
            hasAnswerablePendingRequest: true,
            isComputePending: true,
        }),
        {
            locked: true,
            reason: 'Answer the pending request to send',
            presentation: 'paused',
        },
    )
})
