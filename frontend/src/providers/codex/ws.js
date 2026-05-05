// frontend/src/providers/codex/ws.js
//
// Codex provider WebSocket surface — outbound senders + inbound dispatcher.
// Mirrors ``providers/claude_code/ws.js`` so the registry pattern in
// ``providers/index.js`` stays uniform.

import { sendWsMessage } from '../../composables/useWebSocket'
import { useCodexStore } from './store'

// ─── Outbound senders ────────────────────────────────────────────────────

/**
 * Force the backend to re-check Codex CLI auth state and broadcast the
 * result back via ``codex:auth_updated``.
 * @returns {boolean} - True if message was sent
 */
export function sendCheckAuth() {
    return sendWsMessage({ type: 'codex:check_auth' })
}

// ─── Inbound handler ─────────────────────────────────────────────────────

/**
 * Dispatch a ``codex:<action>`` payload to its handler.
 * Called from the generic ``useWebSocket`` dispatcher.
 */
export const codexWsHandler = {
    handle(action, msg) {
        switch (action) {
            case 'auth_updated':
                useCodexStore().setAuthenticated(msg.authenticated)
                break
            default:
                console.warn(`[codex:ws] no handler for action "${action}"`, msg)
        }
    },
}
