import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER } from '../../constants'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'
import { useClaudeCodeStore } from './store'

// Claude CLI's built-in slash commands. Hardcoded here because the CLI
// never exposes the list programmatically; entries are sourced from the
// CLI documentation.
const BUILTIN_SLASH_COMMANDS = [
    { name: 'compact', plugin_name: null, source: 'builtin', is_global: true, description: 'Clear conversation history but keep a summary in context', argument_hint: '[instructions for summarization]' },
    { name: 'cost', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the cost of the current session', argument_hint: null },
    { name: 'context', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the current context window usage', argument_hint: null },
    { name: 'init', plugin_name: null, source: 'builtin', is_global: true, description: 'Initialize a new CLAUDE.md file with codebase documentation', argument_hint: null },
    { name: 'loop', plugin_name: null, source: 'builtin', is_global: true, description: "Run a prompt or slash command on a recurring interval until the session ends (e.g. /loop 5m /foo, defaults to 10m)", argument_hint: '[interval] [command or prompt]' },
]

// Map of synced setting keys (the wire/storage names) → store action that
// applies the value. Used by both ``applySyncedSettings`` (input) and
// ``getSyncedSettings`` (output) so the two sides can never drift apart.
const SYNCED_SETTING_KEYS_TO_STORE = {
    claudeCodeDefaultPermissionMode: { setter: 'setDefaultPermissionMode', getter: 'defaultPermissionMode' },
    claudeCodeDefaultModel:          { setter: 'setDefaultModel',          getter: 'defaultModel' },
    claudeCodeDefaultContextMax:     { setter: 'setDefaultContextMax',     getter: 'defaultContextMax' },
    claudeCodeDefaultEffort:         { setter: 'setDefaultEffort',         getter: 'defaultEffort' },
    claudeCodeDefaultThinking:       { setter: 'setDefaultThinking',       getter: 'defaultThinking' },
    claudeCodeDefaultClaudeInChrome: { setter: 'setDefaultClaudeInChrome', getter: 'defaultClaudeInChrome' },
}

// Per-field choice catalogue for the Claude Code provider.
// Field names match the snake_case wire names (``thinking_enabled``, not
// ``thinking``); the preset shape uses ``thinking`` and is translated at
// the lookup boundary by callers.
//
// Values are stored in their natural type (string for permission/effort,
// boolean for thinking/chrome, integer for context_max). UI components
// stringify when binding to ``<wa-select>`` and re-parse on change.
//
// ``selected_model`` is intentionally absent: the model list is served via
// the model registry (see ``getModelRegistry``) which carries additional
// metadata (latest, retirement_date, capability flags).
const AGENT_SETTINGS_CHOICES = {
    permission_mode: [
        {
            value: PERMISSION_MODE.DEFAULT,
            label: 'Default',
            description: 'Prompts for permission on first use of each tool',
        },
        {
            value: PERMISSION_MODE.ACCEPT_EDITS,
            label: 'Accept Edits',
            description: 'Auto-accepts file edit permissions',
        },
        {
            value: PERMISSION_MODE.PLAN,
            label: 'Plan',
            description: 'Read-only: Claude can analyze but not modify files',
        },
        {
            value: PERMISSION_MODE.DONT_ASK,
            label: "Don't Ask",
            description: 'Auto-denies tools unless pre-approved via permission rules',
        },
        {
            value: PERMISSION_MODE.BYPASS,
            label: 'Bypass permissions',
            description: 'Skips all permission prompts',
        },
    ],
    effort: [
        { value: EFFORT.LOW,    label: 'Low',    display_label: 'Low effort' },
        { value: EFFORT.MEDIUM, label: 'Medium', display_label: 'Medium effort' },
        { value: EFFORT.HIGH,   label: 'High',   display_label: 'High effort' },
        { value: EFFORT.X_HIGH, label: 'xHigh',  display_label: 'xHigh effort' },
        { value: EFFORT.MAX,    label: 'Max',    display_label: 'Max effort' },
    ],
    thinking_enabled: [
        { value: true,  label: 'Adaptive', display_label: 'Thinking' },
        { value: false, label: 'Disabled', display_label: 'No thinking' },
    ],
    claude_in_chrome: [
        { value: true,  label: 'Enabled',  display_label: 'Chrome MCP' },
        { value: false, label: 'Disabled', display_label: 'No Chrome MCP' },
    ],
    context_max: [
        { value: CONTEXT_MAX.DEFAULT,  label: '200K' },
        { value: CONTEXT_MAX.EXTENDED, label: '1M' },
    ],
}

export class ClaudeCodeHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    canSendMessage() {
        return useClaudeCodeStore().authenticated !== false
    }

    getBuiltInSlashCommands() {
        return BUILTIN_SLASH_COMMANDS
    }

    getSyncedSettingsKeys() {
        return Object.keys(SYNCED_SETTING_KEYS_TO_STORE)
    }

    applySyncedSettings(settings) {
        if (!settings || typeof settings !== 'object') return
        const store = useClaudeCodeStore()
        for (const [key, { setter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            if (key in settings) store[setter](settings[key])
        }
    }

    getSyncedSettings() {
        const store = useClaudeCodeStore()
        const result = {}
        for (const [key, { getter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            result[key] = store[getter]
        }
        return result
    }

    getAgentSettingsCategories() {
        return useClaudeCodeStore().agentSettingsCategories
    }

    getAgentSettingsChoices() {
        return AGENT_SETTINGS_CHOICES
    }

    /**
     * Build a human-friendly label for a Claude Code ``selected_model`` value.
     * "opus" → "Opus", "opus-4.5" → "Opus 4.5", "sonnet" → "Sonnet"
     */
    getModelLabel(selectedModel) {
        if (!selectedModel) return ''
        if (selectedModel.includes('-')) {
            const [model, version] = selectedModel.split('-', 2)
            return `${model.charAt(0).toUpperCase() + model.slice(1)} ${version}`
        }
        return selectedModel.charAt(0).toUpperCase() + selectedModel.slice(1)
    }

    // ─── Model registry & capability flags ───────────────────────────────
    //
    // Wrap reads against the per-provider model registry held by the store.
    // Mirrors the backend ``selected_model_supports_*`` helpers: when the
    // explicit ``selectedModel`` is unknown to the registry, fall back to the
    // current synced default model. The conservative last-resort answer is
    // ``false`` so optional features aren't silently advertised when the
    // registry hasn't been seeded yet.

    getModelRegistry() {
        return useClaudeCodeStore().modelRegistry
    }

    _resolveRegistryEntry(selectedModel) {
        const store = useClaudeCodeStore()
        const registry = store.modelRegistry
        let entry = selectedModel ? registry.find(e => e.selected_model === selectedModel) : undefined
        if (!entry) {
            const defaultModel = store.defaultModel
            if (defaultModel) entry = registry.find(e => e.selected_model === defaultModel)
        }
        return entry
    }

    modelSupports1m(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_1m : false
    }

    modelSupportsEffortXhigh(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_effort_xhigh : false
    }

    modelSupportsEffortMax(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_effort_max : false
    }

    /**
     * Pipeline mirroring the backend ``ClaudeCodeHelpers.enforce_agent_settings_consistency``:
     *
     * 1. Auto-upgrade ``selectedModel`` when retired.
     * 2. Cap ``contextMax`` to ``DEFAULT`` when the (post-upgrade) model
     *    doesn't support 1M context.
     * 3. Demote ``effort``: ``MAX`` → ``X_HIGH`` (or ``HIGH`` if xhigh is
     *    also unsupported), then ``X_HIGH`` → ``HIGH`` when unsupported.
     *
     * Fields not in the input are left absent in the output. ``thinkingEnabled``,
     * ``claudeInChrome`` and ``permissionMode`` are passed through.
     */
    enforceAgentSettingsConsistency(settings) {
        const result = { ...settings }

        if ('selectedModel' in result) {
            const upgrade = this.getRetiredModelUpgrade(result.selectedModel)
            if (upgrade) result.selectedModel = upgrade
        }
        const model = result.selectedModel

        if (result.contextMax === CONTEXT_MAX.EXTENDED && !this.modelSupports1m(model)) {
            result.contextMax = CONTEXT_MAX.DEFAULT
        }

        if (result.effort === EFFORT.MAX && !this.modelSupportsEffortMax(model)) {
            result.effort = this.modelSupportsEffortXhigh(model) ? EFFORT.X_HIGH : EFFORT.HIGH
        }
        if (result.effort === EFFORT.X_HIGH && !this.modelSupportsEffortXhigh(model)) {
            result.effort = EFFORT.HIGH
        }

        return result
    }

    /**
     * Promote the effective window from DEFAULT to EXTENDED when the session
     * has burned past 85% of the 200K window AND the (possibly overridden)
     * model supports 1M. Single source of truth for the settings selector,
     * the header progress ring, and the value sent to the backend.
     */
    getEffectiveContextMax(session, overrideModel = undefined) {
        const store = useClaudeCodeStore()
        const baseValue = session?.context_max ?? store.defaultContextMax
        if (baseValue !== CONTEXT_MAX.DEFAULT) return baseValue
        const model = overrideModel !== undefined ? overrideModel : (session?.selected_model ?? store.defaultModel)
        if (!this.modelSupports1m(model)) return baseValue
        if ((session?.context_usage ?? 0) > CONTEXT_MAX.DEFAULT * 0.85) return CONTEXT_MAX.EXTENDED
        return baseValue
    }

    /**
     * If ``selectedModel`` is retired (past retirement date and not the
     * latest), return the next-higher version in the same family. Otherwise
     * ``null``. Used at render/send time to correct stale session settings.
     */
    getRetiredModelUpgrade(selectedModel) {
        if (!selectedModel) return null
        const registry = useClaudeCodeStore().modelRegistry
        const entry = registry.find(e => e.selected_model === selectedModel)
        if (!entry || entry.latest || !entry.retirement_date) return null
        if (new Date(entry.retirement_date + 'T00:00:00') >= new Date()) return null
        const family = registry
            .filter(e => e.model === entry.model)
            .sort((a, b) => {
                const av = a.version.split('.').map(Number)
                const bv = b.version.split('.').map(Number)
                return av[0] - bv[0] || (av[1] ?? 0) - (bv[1] ?? 0)
            })
        const currentParts = entry.version.split('.').map(Number)
        for (const candidate of family) {
            const cp = candidate.version.split('.').map(Number)
            if (cp[0] > currentParts[0] || (cp[0] === currentParts[0] && (cp[1] ?? 0) > (currentParts[1] ?? 0))) {
                return candidate.selected_model
            }
        }
        return null
    }
}

export const claudeCodeHelpers = new ClaudeCodeHelpers()
