// frontend/src/providers/claude_code/ws.js
//
// Mirror of the backend ``providers/claude_code/ws.py``: this module owns
// the WebSocket surface for the ``claude_code`` provider — both the
// inbound ``claude_code:<action>`` handlers and the outbound senders that
// emit ``claude_code:<action>`` messages.
//
// Generic ``useWebSocket`` only knows how to dispatch a provider-prefixed
// message to the right handler via the registry in ``providers/index.js``;
// it never branches on a specific provider key.

import { sendWsMessage } from '../../composables/useWebSocket'
import { useClaudeCodeStore } from './store'

// ─── Outbound senders ────────────────────────────────────────────────────

/**
 * Force the backend to re-check Claude CLI auth state and broadcast the
 * result back via ``claude_code:auth_updated``.
 * @returns {boolean} - True if message was sent
 */
export function sendCheckAuth() {
    return sendWsMessage({ type: 'claude_code:check_auth' })
}

// ─── Inbound handler ─────────────────────────────────────────────────────

/**
 * Dispatch a ``claude_code:<action>`` payload to its handler.
 * Called from the generic ``useWebSocket`` dispatcher.
 */
export const claudeCodeWsHandler = {
    handle(action, msg) {
        switch (action) {
            case 'auth_updated':
                // Claude CLI auth state changed (or initial push on WS connect)
                useClaudeCodeStore().setAuthenticated(msg.authenticated)
                break
            default:
                console.warn(`[claude_code:ws] no handler for action "${action}"`, msg)
        }
    },
}
