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
import { toast } from '../../composables/useToast'
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

/**
 * Push the Claude settings presets config to the backend for persistence.
 * The backend will broadcast the updated config to all connected clients
 * via ``claude_code:settings_presets_updated``.
 * @param {Object} config - The Claude settings presets config
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendUpdateSettingsPresets(config) {
    return sendWsMessage({ type: 'claude_code:update_settings_presets', config })
}

// Pending resolve callback for the usage file validation request/response
// round-trip (claude_code:validate_usage_file → claude_code:usage_file_validated).
let _usageFileValidateResolve = null

/**
 * Validate a usage JSON file path (read mode) on the backend. Resolves
 * once the matching ``claude_code:usage_file_validated`` reply arrives.
 * @param {string} filePath - The file path to validate
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export function sendValidateUsageFile(filePath) {
    return new Promise((resolve) => {
        _usageFileValidateResolve = resolve
        const sent = sendWsMessage({ type: 'claude_code:validate_usage_file', file_path: filePath })
        if (!sent) {
            _usageFileValidateResolve = null
            resolve({ valid: false, message: 'Not connected' })
        }
    })
}

// ─── Anthropic statuspage helpers ────────────────────────────────────────

// Persists the last Anthropic statuspage value seen, so a page refresh
// during an ongoing outage doesn't re-toast the same notification.
const ANTHROPIC_STATUS_STORAGE_KEY = 'twicc-claude-code-anthropic-status'

// Clean up the previous storage key (renamed from 'twicc-claude-status' once
// the value was scoped to a specific provider + service).
localStorage.removeItem('twicc-claude-status')

const ANTHROPIC_STATUS_MESSAGES = {
    degraded_performance: {
        type: 'warning',
        message: 'Claude Code is currently experiencing degraded performance on Anthropic\'s side',
    },
    partial_outage: {
        type: 'warning',
        message: 'Claude Code is currently experiencing a partial outage on Anthropic\'s side',
    },
    major_outage: {
        type: 'error',
        message: 'Claude Code is currently experiencing a major outage on Anthropic\'s side',
    },
    under_maintenance: {
        type: 'info',
        message: 'Claude Code is currently under maintenance on Anthropic\'s side',
    },
}

/**
 * Show a persistent toast when Claude Code is not operational, or a
 * resolution toast when it returns to operational. Deduplication is
 * done via localStorage so page refreshes during an ongoing outage
 * don't re-trigger the notification.
 */
function renderAnthropicStatusToast(msg) {
    const { status } = msg
    if (!status) return

    const lastStatus = localStorage.getItem(ANTHROPIC_STATUS_STORAGE_KEY)
    if (status === lastStatus) return
    localStorage.setItem(ANTHROPIC_STATUS_STORAGE_KEY, status)

    const statusLink = '<a href="https://status.claude.com/" target="_blank" rel="noopener" style="color: inherit; text-decoration: underline;">status.claude.com</a>'

    if (status === 'operational') {
        // Only show the "resolved" toast if there was a previous non-operational status.
        // On first load (no localStorage entry) or if already operational, skip it.
        if (lastStatus && lastStatus !== 'operational') {
            toast.custom({
                type: 'success',
                title: 'Anthropic status update',
                html: `Claude Code issues on Anthropic's side are now resolved — ${statusLink}`,
                duration: Infinity,
            })
        }
    } else {
        const config = ANTHROPIC_STATUS_MESSAGES[status]
        if (config) {
            toast.custom({
                type: config.type,
                title: 'Anthropic status update',
                html: `${config.message} — ${statusLink}`,
                duration: Infinity,
            })
        }
    }
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
            case 'anthropic_status':
                useClaudeCodeStore().setAnthropicStatus(msg.status)
                renderAnthropicStatusToast(msg)
                break
            case 'usage_file_validated':
                if (_usageFileValidateResolve) {
                    _usageFileValidateResolve({ valid: msg.valid, message: msg.message })
                    _usageFileValidateResolve = null
                }
                break
            case 'settings_presets_updated':
                // Lazy import avoids the circular dep
                // (ws.js → claudeSettingsPresets store → ws.js sender).
                import('../../stores/claudeSettingsPresets').then(({ useClaudeSettingsPresetsStore }) => {
                    useClaudeSettingsPresetsStore().applyConfig(msg.config)
                })
                break
            default:
                console.warn(`[claude_code:ws] no handler for action "${action}"`, msg)
        }
    },
}
