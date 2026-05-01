// frontend/src/constants.js

/**
 * Shared constants for the application.
 */

/**
 * Number of items to load at start (first N and last N) when viewing a session.
 * Also used during reconciliation to limit how many new items we fetch at once.
 */
export const INITIAL_ITEMS_COUNT = 100

/**
 * Display mode values for session items.
 * - conversation: Show only user messages + last assistant message before each user message
 * - simplified: Show level 1, collapse level 2 groups, hide level 3
 * - normal: Show levels 1 and 2, hide level 3
 * - debug: Show all items (levels 1, 2, 3)
 */
export const DISPLAY_MODE = {
    DEBUG: 'debug',
    NORMAL: 'normal',
    SIMPLIFIED: 'simplified',
    CONVERSATION: 'conversation',
}

export const DEFAULT_DISPLAY_MODE = DISPLAY_MODE.NORMAL

/**
 * Color scheme values.
 * - system: Follow system preference (prefers-color-scheme)
 * - light: Force light mode
 * - dark: Force dark mode
 */
export const COLOR_SCHEME = {
    SYSTEM: 'system',
    LIGHT: 'light',
    DARK: 'dark',
}

export const DEFAULT_COLOR_SCHEME = COLOR_SCHEME.SYSTEM

/**
 * Session time format values.
 * - time: Show formatted time (smart format: hour if recent, date otherwise)
 * - relative_short: Show relative time with short format ("2 hr. ago")
 * - relative_narrow: Show relative time with narrow format ("2h ago")
 */
export const SESSION_TIME_FORMAT = {
    TIME: 'time',
    RELATIVE_SHORT: 'relative_short',
    RELATIVE_NARROW: 'relative_narrow',
}

export const DEFAULT_SESSION_TIME_FORMAT = SESSION_TIME_FORMAT.TIME

/**
 * Default maximum number of sessions kept alive in the cache (Vue KeepAlive).
 * Each cached session preserves its DOM, scroll position, and component state
 * for instant switching. Cost is ~150-500 KB per session (more with terminal).
 * Can be adjusted per device in settings.
 */
export const DEFAULT_MAX_CACHED_SESSIONS = 20

/**
 * Context window size values.
 * Controls the maximum context window size for Claude sessions.
 */
export const CONTEXT_MAX = {
    DEFAULT: 200_000,
    EXTENDED: 1_000_000,
}

/**
 * Human-friendly labels for each context max value.
 */
export const CONTEXT_MAX_LABELS = {
    [CONTEXT_MAX.DEFAULT]: '200K',
    [CONTEXT_MAX.EXTENDED]: '1M',
}

/**
 * @deprecated Use session.context_max instead. Kept only for backward compatibility.
 */
export const MAX_CONTEXT_TOKENS = 200_000

/**
 * Tool names that spawn subagent sessions.
 * "Task" is the legacy name, "Agent" is the new one — both behave identically.
 */
export const AGENT_TOOL_NAMES = new Set(['Task', 'Agent'])

/**
 * Display level values for session items (matches backend ItemDisplayLevel enum).
 * - ALWAYS: Always shown in all modes
 * - COLLAPSIBLE: Shown in Normal, grouped in Simplified
 * - DEBUG_ONLY: Only shown in Debug mode
 */
export const DISPLAY_LEVEL = {
    ALWAYS: 1,
    COLLAPSIBLE: 2,
    DEBUG_ONLY: 3,
}

/**
 * Synthetic items injected client-side (not from backend).
 * Each entry has:
 * - lineNum: negative to avoid collision with real backend line numbers (1-based)
 * - kind: string identifier used as syntheticKind and data-synthetic-kind attribute
 */
export const SYNTHETIC_ITEM = {
    OPTIMISTIC_USER_MESSAGE: { lineNum: -2000, kind: 'optimistic-user-message' },
    STARTING_ASSISTANT_MESSAGE: { lineNum: -1500, kind: 'starting-assistant-message' },
    // Streaming blocks use baseLineNum - blockIndex as their lineNum (e.g., -1000, -1001, ...)
    STREAMING_BLOCK: { baseLineNum: -1000, kind: 'streaming-block' },
    WORKING_ASSISTANT_MESSAGE: { lineNum: -500, kind: 'working-assistant-message' },
}

/**
 * Process state values (matches backend ProcessState enum).
 */
export const PROCESS_STATE = {
    STARTING: 'starting',
    ASSISTANT_TURN: 'assistant_turn',
    USER_TURN: 'user_turn',
    DEAD: 'dead',
}

/**
 * Human-friendly names for each process state.
 */
export const PROCESS_STATE_NAMES = {
    [PROCESS_STATE.STARTING]: 'Starting',
    [PROCESS_STATE.ASSISTANT_TURN]: 'Assistant turn',
    [PROCESS_STATE.USER_TURN]: 'User turn',
    [PROCESS_STATE.DEAD]: 'Dead',
}

/**
 * CSS color variables for each process state.
 * Used for consistent coloring across components (indicators, text, etc.).
 */
export const PROCESS_STATE_COLORS = {
    [PROCESS_STATE.STARTING]: 'var(--wa-color-warning-60)',
    [PROCESS_STATE.ASSISTANT_TURN]: 'var(--wa-color-blue-60)',
    [PROCESS_STATE.USER_TURN]: 'var(--wa-color-success-60)',
    [PROCESS_STATE.DEAD]: 'var(--wa-color-danger-60)',
}

/**
 * Backend provider values (matches backend Provider enum).
 * Identifies the backend that produced a session.
 */
export const PROVIDER = {
    CLAUDE_CODE: 'claude_code',
}

/**
 * Permission mode values (matches SDK PermissionMode).
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
 * Human-friendly labels for each permission mode.
 */
export const PERMISSION_MODE_LABELS = {
    [PERMISSION_MODE.DEFAULT]: 'Default',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Accept Edits',
    [PERMISSION_MODE.PLAN]: 'Plan',
    [PERMISSION_MODE.DONT_ASK]: "Don't Ask",
    [PERMISSION_MODE.BYPASS]: 'Bypass permissions',
}

/**
 * Short descriptions for each permission mode (for tooltips/settings).
 */
export const PERMISSION_MODE_DESCRIPTIONS = {
    [PERMISSION_MODE.DEFAULT]: 'Prompts for permission on first use of each tool',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Auto-accepts file edit permissions',
    [PERMISSION_MODE.PLAN]: 'Read-only: Claude can analyze but not modify files',
    [PERMISSION_MODE.DONT_ASK]: 'Auto-denies tools unless pre-approved via permission rules',
    [PERMISSION_MODE.BYPASS]: 'Skips all permission prompts',
}

/**
 * Build a human-friendly label for a selected_model value.
 * "opus" → "Opus", "opus-4.5" → "Opus 4.5", "sonnet" → "Sonnet"
 */
export function getModelLabel(selectedModel) {
    if (!selectedModel) return ''
    if (selectedModel.includes('-')) {
        const [model, version] = selectedModel.split('-', 2)
        return `${model.charAt(0).toUpperCase() + model.slice(1)} ${version}`
    }
    return selectedModel.charAt(0).toUpperCase() + selectedModel.slice(1)
}

/**
 * Effort level values (matches SDK effort parameter).
 * Controls the depth of thinking for Claude's responses.
 */
export const EFFORT = {
    MAX: 'max',
    X_HIGH: 'xhigh',
    HIGH: 'high',
    MEDIUM: 'medium',
    LOW: 'low',
}

/**
 * Human-friendly labels for each effort level.
 */
export const EFFORT_LABELS = {
    [EFFORT.LOW]: 'Low',
    [EFFORT.MEDIUM]: 'Medium',
    [EFFORT.HIGH]: 'High',
    [EFFORT.X_HIGH]: 'xHigh',
    [EFFORT.MAX]: 'Max',
}

/**
 * Display input text for each effort level (shown in the collapsed select).
 */
export const EFFORT_DISPLAY_LABELS = {
    [EFFORT.LOW]: 'Low effort',
    [EFFORT.MEDIUM]: 'Medium effort',
    [EFFORT.HIGH]: 'High effort',
    [EFFORT.X_HIGH]: 'xHigh effort',
    [EFFORT.MAX]: 'Max effort',
}

/**
 * Thinking mode values.
 * Controls whether extended thinking (adaptive) is enabled.
 */
export const THINKING = {
    ENABLED: true,
    DISABLED: false,
}

/**
 * Human-friendly labels for each thinking mode.
 */
export const THINKING_LABELS = {
    [THINKING.ENABLED]: 'Adaptive',
    [THINKING.DISABLED]: 'Disabled',
}

/**
 * Display input text for each thinking mode (shown in the collapsed select).
 */
export const THINKING_DISPLAY_LABELS = {
    [THINKING.ENABLED]: 'Thinking',
    [THINKING.DISABLED]: 'No thinking',
}

/**
 * Claude in Chrome MCP mode values.
 * Controls whether the built-in Chrome MCP is activated.
 */
export const CLAUDE_IN_CHROME = {
    ENABLED: true,
    DISABLED: false,
}

/**
 * Human-friendly labels for each Claude in Chrome mode.
 */
export const CLAUDE_IN_CHROME_LABELS = {
    [CLAUDE_IN_CHROME.ENABLED]: 'Enabled',
    [CLAUDE_IN_CHROME.DISABLED]: 'Disabled',
}

/**
 * Display input text for each Claude in Chrome mode (shown in the collapsed select).
 */
export const CLAUDE_IN_CHROME_DISPLAY_LABELS = {
    [CLAUDE_IN_CHROME.ENABLED]: 'Chrome MCP',
    [CLAUDE_IN_CHROME.DISABLED]: 'No Chrome MCP',
}

/**
 * Web Awesome theme values.
 * Controls the visual theme applied to Web Awesome components.
 */
export const WA_THEME = {
    DEFAULT: 'default',
    SHOELACE: 'shoelace',
    AWESOME: 'awesome',
}

export const WA_THEME_LABELS = {
    [WA_THEME.DEFAULT]: 'Default',
    [WA_THEME.SHOELACE]: 'Shoelace',
    [WA_THEME.AWESOME]: 'Awesome',
}

export const WA_THEME_DEFAULT_PALETTE = {
    [WA_THEME.AWESOME]: 'bright',
    [WA_THEME.DEFAULT]: 'default',
    [WA_THEME.SHOELACE]: 'shoelace',
}

/**
 * Web Awesome brand color values.
 * Controls the accent/brand color used throughout the UI.
 */
export const WA_BRAND = {
    BLUE: 'blue',
    RED: 'red',
    ORANGE: 'orange',
    YELLOW: 'yellow',
    GREEN: 'green',
    CYAN: 'cyan',
    INDIGO: 'indigo',
    PURPLE: 'purple',
    PINK: 'pink',
    GRAY: 'gray',
}

export const WA_BRAND_LABELS = {
    [WA_BRAND.BLUE]: 'Blue',
    [WA_BRAND.RED]: 'Red',
    [WA_BRAND.ORANGE]: 'Orange',
    [WA_BRAND.YELLOW]: 'Yellow',
    [WA_BRAND.GREEN]: 'Green',
    [WA_BRAND.CYAN]: 'Cyan',
    [WA_BRAND.INDIGO]: 'Indigo',
    [WA_BRAND.PURPLE]: 'Purple',
    [WA_BRAND.PINK]: 'Pink',
    [WA_BRAND.GRAY]: 'Gray',
}

/**
 * Settings keys that are synced across devices via backend settings.json.
 * All other settings remain local to the browser (localStorage only).
 */
export const SYNCED_SETTINGS_KEYS = new Set([
    'titleGenerationEnabled',
    'titleAutoApply',
    'titleSystemPrompt',
    'claudeCodeDefaultPermissionMode',
    'claudeCodeDefaultModel',
    'claudeCodeDefaultEffort',
    'claudeCodeDefaultThinking',
    'claudeCodeDefaultClaudeInChrome',
    'claudeCodeDefaultContextMax',
    'autoUnpinOnArchive',
    'terminalUseTmux',
    'terminalTmuxConfigPath',
    'waTheme',
    'waBrand',
    'claudeCodeUsageReadFileEnabled',
    'claudeCodeUsageReadFilePath',
    'claudeCodeUsageDumpFileEnabled',
    'claudeCodeUsageDumpFilePath',
])
