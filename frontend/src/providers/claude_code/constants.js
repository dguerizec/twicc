// frontend/src/providers/claude_code/constants.js
//
// Provider-specific value enums for the Claude Code agent settings. These
// mirror the SDK / backend wire values and are used by Claude-only logic
// (capability gates, ``enforceAgentSettingsConsistency``, the
// ``AGENT_SETTINGS_CHOICES`` catalogue) — generic code never needs them.

/**
 * Context window size values exposed by the Claude Code provider.
 */
export const CONTEXT_MAX = {
    DEFAULT: 200_000,
    EXTENDED: 1_000_000,
}

/**
 * Permission mode values (matches Claude Code SDK ``PermissionMode``).
 * Controls how Claude Code handles tool permission prompts.
 */
export const PERMISSION_MODE = {
    DEFAULT: 'default',
    ACCEPT_EDITS: 'acceptEdits',
    PLAN: 'plan',
    DONT_ASK: 'dontAsk',
    BYPASS: 'bypassPermissions',
}

/**
 * Effort level values (matches Claude Code SDK ``effort`` parameter).
 * Controls the depth of thinking for Claude's responses.
 */
export const EFFORT = {
    MAX: 'max',
    X_HIGH: 'xhigh',
    HIGH: 'high',
    MEDIUM: 'medium',
    LOW: 'low',
}

