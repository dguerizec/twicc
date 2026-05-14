// frontend/src/providers/codex/ws.js
//
// Codex provider WebSocket surface — outbound senders + inbound dispatcher.
// Mirrors ``providers/claude_code/ws.js`` so the registry pattern in
// ``providers/index.js`` stays uniform.

import { sendWsMessage } from '../../composables/useWebSocket'
import { toast } from '../../composables/useToast'
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

/**
 * Respond to a pending Codex tool-approval request raised by the SDK
 * via the sync ↔ async bridge in CodexAgent. The ``responseData`` shape
 * must already match the Codex wire format (see backend spec §9.3/§9.5):
 *
 *   commandExecution / fileChange:
 *     { tool_name: 'commandExecution' | 'fileChange',
 *       decision: 'accept' | 'acceptForSession' | 'decline' | 'cancel'
 *                | { acceptWithExecpolicyAmendment: {...} }
 *                | { applyNetworkPolicyAmendment: {...} } }
 *
 *   permissions:
 *     { tool_name: 'permissions',
 *       permissions: {...}, scope: 'turn' | 'session',
 *       strictAutoReview?: boolean }
 *
 * The backend ``CodexWSHandler._build_codex_response`` validates this
 * strictly and falls back to a safe default on any malformed payload, so
 * the SDK is never left waiting.
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(sessionId, requestId, responseData) {
    return sendWsMessage({
        type: 'codex:pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
}

// ─── OpenAI statuspage helpers ───────────────────────────────────────────

// Persists the last OpenAI statuspage value seen, so a page refresh during
// an ongoing outage doesn't re-toast the same notification.
const OPENAI_STATUS_STORAGE_KEY = 'twicc-codex-openai-status'

const OPENAI_STATUS_MESSAGES = {
    degraded_performance: {
        type: 'warning',
        message: 'Codex is currently experiencing degraded performance on OpenAI\'s side',
    },
    partial_outage: {
        type: 'warning',
        message: 'Codex is currently experiencing a partial outage on OpenAI\'s side',
    },
    major_outage: {
        type: 'error',
        message: 'Codex is currently experiencing a major outage on OpenAI\'s side',
    },
    under_maintenance: {
        type: 'info',
        message: 'Codex is currently under maintenance on OpenAI\'s side',
    },
}

/**
 * Show a persistent toast when Codex is not operational, or a resolution
 * toast when it returns to operational. Deduplication is done via
 * localStorage so page refreshes during an ongoing outage don't re-trigger
 * the notification.
 */
function renderOpenaiStatusToast(msg) {
    const { status } = msg
    if (!status) return

    const lastStatus = localStorage.getItem(OPENAI_STATUS_STORAGE_KEY)
    if (status === lastStatus) return
    localStorage.setItem(OPENAI_STATUS_STORAGE_KEY, status)

    const statusLink = '<a href="https://status.openai.com/" target="_blank" rel="noopener" style="color: inherit; text-decoration: underline;">status.openai.com</a>'

    if (status === 'operational') {
        // Only show the "resolved" toast if there was a previous non-operational status.
        // On first load (no localStorage entry) or if already operational, skip it.
        if (lastStatus && lastStatus !== 'operational') {
            toast.custom({
                type: 'success',
                title: 'OpenAI status update',
                html: `Codex issues on OpenAI's side are now resolved — ${statusLink}`,
                duration: Infinity,
            })
        }
    } else {
        const config = OPENAI_STATUS_MESSAGES[status]
        if (config) {
            toast.custom({
                type: config.type,
                title: 'OpenAI status update',
                html: `${config.message} — ${statusLink}`,
                duration: Infinity,
            })
        }
    }
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
            case 'openai_status':
                useCodexStore().setOpenaiStatus(msg.status)
                renderOpenaiStatusToast(msg)
                break
            default:
                console.warn(`[codex:ws] no handler for action "${action}"`, msg)
        }
    },
}
