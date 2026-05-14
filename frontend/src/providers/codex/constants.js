// frontend/src/providers/codex/constants.js
//
// Provider-specific value enums for the Codex agent settings. These mirror
// the wire values stored on ``Session`` rows and the synced settings, and
// are used by the Codex-only choice catalogue.

/**
 * Context window size values exposed by the Codex provider.
 */
export const CONTEXT_MAX = {
    DEFAULT: 272_000,
}

/**
 * Permission mode values for Codex sessions. Mirrors the Codex CLI's
 * approval modes (read-only / auto / autonomous / yolo).
 */
export const PERMISSION_MODE = {
    READ_ONLY: 'read_only',
    STRICT: 'strict',
    AUTO: 'auto',
    AUTONOMOUS: 'autonomous',
    YOLO: 'yolo',
}

/**
 * Effort level values for Codex sessions.
 */
export const EFFORT = {
    LOW: 'low',
    MEDIUM: 'medium',
    HIGH: 'high',
    X_HIGH: 'xhigh',
}
