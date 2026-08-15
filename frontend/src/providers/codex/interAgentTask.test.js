// frontend/src/providers/codex/interAgentTask.test.js
//
// Run with:  node --test src/providers/codex/interAgentTask.test.js   (from the frontend dir)
//
// A Codex multi-agent v2 subagent has no user message: its prompt arrives
// as a `NEW_TASK` inter-agent envelope whose payload is normally
// encrypted. These cover the markdown we compose in its place — and, just
// as importantly, the envelopes that must NOT be turned into one.

import test from 'node:test'
import assert from 'node:assert/strict'

import { interAgentTaskMarkdown } from './interAgentTask.js'

const ENCRYPTED_BLOCK = { type: 'encrypted_content', encrypted_content: 'gAAAAABmZmZm' }

/** A `response_item.agent_message` line as Codex persists it. */
function envelope(text, { encrypted = true } = {}) {
    const content = [{ type: 'input_text', text }]
    if (encrypted) content.push(ENCRYPTED_BLOCK)
    return { type: 'response_item', payload: { type: 'agent_message', content } }
}

function newTask({ path = '/root/tweak_display_test', sender = '/root', clear = '' } = {}) {
    const text = [
        'Message Type: NEW_TASK',
        `Task name: ${path}`,
        `Sender: ${sender}`,
        'Payload:',
        clear,
    ].join('\n')
    return envelope(text, { encrypted: !clear })
}

test('an encrypted task shows its name and says the prompt is unreadable', () => {
    assert.equal(
        interAgentTaskMarkdown(newTask()),
        '**Tweak display test**\n\n*The prompt is encrypted by Codex and cannot be displayed.*',
    )
})

test('a payload sent in the clear is rendered instead of the notice', () => {
    assert.equal(
        interAgentTaskMarkdown(newTask({ clear: 'Do the thing, **carefully**.' })),
        '**Tweak display test**\n\nDo the thing, **carefully**.',
    )
})

test('the routing header never leaks into the body', () => {
    const out = interAgentTaskMarkdown(newTask({ path: '/root/impl/review', sender: '/root/impl' }))
    assert.doesNotMatch(out, /Sender|Task name|Message Type|\/root/)
    assert.match(out, /^\*\*Review\*\*/)
})

test('mid-flight and answer envelopes are not opening prompts', () => {
    const message = envelope('Message Type: MESSAGE\nTask name: /root\nSender: /root/x\nPayload:\n')
    const answer = envelope('Message Type: FINAL_ANSWER\nTask name: /root\nSender: /root/x\nPayload:\ndone', { encrypted: false })

    assert.equal(interAgentTaskMarkdown(message), null)
    assert.equal(interAgentTaskMarkdown(answer), null)
})

test('ordinary lines are left alone', () => {
    assert.equal(interAgentTaskMarkdown({ type: 'event_msg', payload: { type: 'user_message', message: 'hi' } }), null)
    assert.equal(interAgentTaskMarkdown({}), null)
    assert.equal(interAgentTaskMarkdown(null), null)
})
