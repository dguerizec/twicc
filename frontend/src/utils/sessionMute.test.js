// frontend/src/utils/sessionMute.test.js
//
// Covers session notification mute persistence. A missing optimistic update,
// wrong PATCH request, response merge, or rollback must fail these tests.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { applySessionMuteOnUserTurn } from './sessionMute.js'

test('applies the mute state immediately and merges the PATCH response', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            project_id: 'project_1',
            mute_on_user_turn: false,
            title: 'Before response',
        },
    }
    let resolveRequest
    const request = new Promise(resolve => { resolveRequest = resolve })
    const apiFetch = async (url, options) => {
        assert.equal(url, '/api/projects/project_1/sessions/session_1/')
        assert.equal(options.method, 'PATCH')
        assert.deepEqual(options.headers, { 'Content-Type': 'application/json' })
        assert.deepEqual(JSON.parse(options.body), { mute_on_user_turn: true })
        await request
        return {
            ok: true,
            json: async () => ({ mute_on_user_turn: true, title: 'From server' }),
        }
    }

    const pending = applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true)
    assert.equal(sessions.session_1.mute_on_user_turn, true)
    resolveRequest()
    await pending

    assert.deepEqual(sessions.session_1, {
        id: 'session_1',
        project_id: 'project_1',
        mute_on_user_turn: true,
        title: 'From server',
    })
})

test('restores the prior mute state when PATCH fails', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            project_id: 'project_1',
            mute_on_user_turn: false,
        },
    }
    const apiFetch = async () => ({
        ok: false,
        json: async () => ({ error: 'Request failed' }),
    })

    await assert.rejects(
        applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true),
        { message: 'Request failed' },
    )

    assert.equal(sessions.session_1.mute_on_user_turn, false)
})
