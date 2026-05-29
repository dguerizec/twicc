import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER, SYNTHETIC_ITEM } from '../../constants'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'
import { useClaudeCodeStore } from './store'
import { getTwiccLaunchPrefix } from '../../utils/twiccLaunch'
import {
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_TEXT_TYPES,
} from '../../utils/fileUtils'

// Claude API per-file ceiling (5 MB) — applies before resize. Beyond this
// the server rejects the request, so the frontend enforces it client-side
// to fail fast on the toast surface instead of waiting for an SDK error.
const CLAUDE_MAX_FILE_BYTES = 5 * 1024 * 1024

function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}

// Claude CLI's built-in commands (invoked with ``/``). Hardcoded here because
// the CLI never exposes the list programmatically; entries are sourced from
// the CLI documentation. Surface order matches the CLI's own.
//
// ``is_builtin`` is a frontend-only sentinel the picker tag uses to render
// the ``(built-in)`` label — these rows never reach the backend, so it
// doesn't need a matching column on ``Command``.
const BUILTIN_COMMANDS = [
    { name: 'compact', plugin_name: null, is_builtin: true, is_global: true, description: 'Clear conversation history but keep a summary in context', argument_hint: '[instructions for summarization]' },
    { name: 'cost', plugin_name: null, is_builtin: true, is_global: true, description: 'Show the cost of the current session', argument_hint: null },
    { name: 'context', plugin_name: null, is_builtin: true, is_global: true, description: 'Show the current context window usage', argument_hint: null },
    { name: 'init', plugin_name: null, is_builtin: true, is_global: true, description: 'Initialize a new CLAUDE.md file with codebase documentation', argument_hint: null },
    { name: 'loop', plugin_name: null, is_builtin: true, is_global: true, description: "Run a prompt or slash command on a recurring interval until the session ends (e.g. /loop 5m /foo, defaults to 10m)", argument_hint: '[interval] [command or prompt]' },
]

// Map of usage-file field names (cross-provider) → Claude Code store
// getter/setter. Mirrors ``FIELD_TO_DEFAULT_STORE_BINDING``: lets the
// generic ``getUsageFileSetting`` / ``setUsageFileSetting`` hooks proxy
// to this provider's own ``useClaudeCodeStore`` refs, so the on-disk
// shape and synced settings stay Claude Code-specific while the UI
// stays provider-agnostic.
const USAGE_FILE_FIELD_TO_STORE_BINDING = {
    read_enabled: { getter: 'usageReadFileEnabled', setter: 'setUsageReadFileEnabled' },
    read_path:    { getter: 'usageReadFilePath',    setter: 'setUsageReadFilePath' },
    dump_enabled: { getter: 'usageDumpFileEnabled', setter: 'setUsageDumpFileEnabled' },
    dump_path:    { getter: 'usageDumpFilePath',    setter: 'setUsageDumpFilePath' },
}

// Map of agent-setting wire names → store getter/setter for the persisted
// default. Used by ``getDefaultValue`` / ``setDefaultValue`` so generic
// surfaces (palette, settings popover) can read/write defaults without
// knowing the field-specific store property names.
const FIELD_TO_DEFAULT_STORE_BINDING = {
    selected_model:   { getter: 'defaultModel',           setter: 'setDefaultModel' },
    effort:           { getter: 'defaultEffort',          setter: 'setDefaultEffort' },
    thinking_enabled: { getter: 'defaultThinking',        setter: 'setDefaultThinking' },
    permission_mode:  { getter: 'defaultPermissionMode',  setter: 'setDefaultPermissionMode' },
    context_max:      { getter: 'defaultContextMax',      setter: 'setDefaultContextMax' },
    claude_in_chrome: { getter: 'defaultClaudeInChrome',  setter: 'setDefaultClaudeInChrome' },
    fast_mode:        { getter: 'defaultFastMode',        setter: 'setDefaultFastMode' },
}

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
    claudeCodeDefaultFastMode:       { setter: 'setDefaultFastMode',       getter: 'defaultFastMode' },
    claudeCodeUsageReadFileEnabled:  { setter: 'setUsageReadFileEnabled',  getter: 'usageReadFileEnabled' },
    claudeCodeUsageReadFilePath:     { setter: 'setUsageReadFilePath',     getter: 'usageReadFilePath' },
    claudeCodeUsageDumpFileEnabled:  { setter: 'setUsageDumpFileEnabled',  getter: 'usageDumpFileEnabled' },
    claudeCodeUsageDumpFilePath:     { setter: 'setUsageDumpFilePath',     getter: 'usageDumpFilePath' },
}

// Statuspage display map: Atlassian status values → footer rendering.
// Matches the status strings broadcast by the backend statuspage task.
const ANTHROPIC_STATUS_DISPLAY = {
    operational:          { label: 'Operational',      modifier: 'ok' },
    degraded_performance: { label: 'Degraded',         modifier: 'warning' },
    partial_outage:       { label: 'Partial outage',   modifier: 'warning' },
    major_outage:         { label: 'Major outage',     modifier: 'error' },
    under_maintenance:    { label: 'Maintenance',      modifier: 'info' },
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
    fast_mode: [
        { value: true,  label: 'Enabled',  display_label: 'Fast mode' },
        { value: false, label: 'Disabled', display_label: 'No fast mode' },
    ],
    context_max: [
        { value: CONTEXT_MAX.DEFAULT,  label: '200K' },
        { value: CONTEXT_MAX.EXTENDED, label: '1M' },
    ],
}

export class ClaudeCodeHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CLAUDE_CODE
    static label = 'Claude'
    static icon = 'claude'

    canSendMessage() {
        return useClaudeCodeStore().authenticated !== false
    }

    getCommandActivationChars() {
        return ['/']
    }

    getPlaceholderAssistantTurnNote() {
        return 'Note: it will not appear in the conversation history.'
    }

    getBuiltInCommands(activationChar) {
        return activationChar === '/' ? BUILTIN_COMMANDS : []
    }

    buildOptimisticUserMessageContent(text, attachments) {
        // Claude Code's user_message JSONL shape: a transport-style envelope
        // ``{ type: 'user', message: { role, content: [...] } }`` whose
        // ``content`` is the same block array the SDK accepts. Images /
        // documents come in already-SDK-shaped, so we just concatenate.
        const content = []
        if (attachments?.images?.length) content.push(...attachments.images)
        if (attachments?.documents?.length) content.push(...attachments.documents)
        content.push({ type: 'text', text })
        return {
            type: 'user',
            syntheticKind: SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind,
            message: { role: 'user', content },
        }
    }

    extractUserMessageText(parsed) {
        const content = parsed?.message?.content
        if (typeof content === 'string') {
            return content.trim() || null
        }
        if (!Array.isArray(content)) return null

        const text = content
            .filter(block => block?.type === 'text' && typeof block.text === 'string')
            .map(block => block.text)
            .join('\n')
            .trim()
        return text || null
    }

    getAuthState() {
        return () => useClaudeCodeStore().authenticated
    }

    getAuthLoginCommand() {
        return `${getTwiccLaunchPrefix()} claude auth login`
    }

    async requestAuthRecheck() {
        // Lazy import: ``./ws`` pulls ``useWebSocket``, which imports back into
        // ``providers/index`` (which imports this module). The lazy form breaks
        // the cycle so HMR can keep doing hot updates.
        const { sendCheckAuth } = await import('./ws')
        sendCheckAuth()
    }

    tracksUsage() {
        return true
    }

    getUsageExternalLink() {
        return { url: 'https://claude.ai/settings/usage', label: 'View usage on claude.ai' }
    }

    getUsageFileSetting(field) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return null
        return useClaudeCodeStore()[binding.getter]
    }

    setUsageFileSetting(field, value) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return
        useClaudeCodeStore()[binding.setter](value)
    }

    getServiceStatus() {
        return () => useClaudeCodeStore().anthropicStatus
    }

    getServiceStatusDisplay(status) {
        const entry = ANTHROPIC_STATUS_DISPLAY[status] ?? { label: status, modifier: 'ok' }
        return {
            ...entry,
            url: 'https://status.claude.com/',
            tooltip: "Claude Code status on Anthropic's side",
        }
    }

    getDefaultValue(field) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return null
        return useClaudeCodeStore()[binding.getter]
    }

    setDefaultValue(field, value) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return
        const store = useClaudeCodeStore()
        store[binding.setter](value)
        // Re-enforce cross-field consistency when the default model changes:
        // the new model may not support the persisted context_max / effort,
        // so they get rolled back to a value the model accepts.
        if (field === 'selected_model') {
            const adjusted = this.enforceAgentSettingsConsistency({
                selectedModel: store.defaultModel,
                contextMax: store.defaultContextMax,
                effort: store.defaultEffort,
            })
            if (adjusted.contextMax !== store.defaultContextMax) store.setDefaultContextMax(adjusted.contextMax)
            if (adjusted.effort !== store.defaultEffort) store.setDefaultEffort(adjusted.effort)
        }
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

    modelSupportsFast(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_fast : false
    }

    /**
     * Pipeline mirroring the backend ``ClaudeCodeHelpers.enforce_agent_settings_consistency``:
     *
     * 1. Auto-upgrade ``selectedModel`` when retired.
     * 2. Cap ``contextMax`` to ``DEFAULT`` when the (post-upgrade) model
     *    doesn't support 1M context.
     * 3. Demote ``effort``: ``MAX`` → ``X_HIGH`` (or ``HIGH`` if xhigh is
     *    also unsupported), then ``X_HIGH`` → ``HIGH`` when unsupported.
     * 4. Clear ``fastMode`` when the (post-upgrade) model doesn't support it
     *    (only supported on Opus 4.6+).
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

        if (result.fastMode && !this.modelSupportsFast(model)) {
            result.fastMode = false
        }

        return result
    }

    /**
     * The auto-promote rule itself: at 200K, with a model that supports 1M,
     * once the session has burned past 85% of the 200K window, promote to 1M.
     * Stateless and parameterised — callers pass the ``contextMax`` they want
     * to evaluate the rule against (the persisted value, or the user's live
     * selection in the popover).
     */
    isContextMaxAutoPromoted(session, contextMax, model) {
        return (
            contextMax === CONTEXT_MAX.DEFAULT
            && this.modelSupports1m(model)
            && (session?.context_usage ?? 0) > CONTEXT_MAX.DEFAULT * 0.85
        )
    }

    /**
     * Resolve a session's effective ``context_max``: the persisted value (or
     * the provider default when null), bumped to 1M by the auto-promote rule
     * when applicable. Single source of truth for the header progress ring
     * and the value the popover sends to the backend.
     */
    getEffectiveContextMax(session, overrideModel = undefined) {
        const store = useClaudeCodeStore()
        const baseValue = session?.context_max ?? store.defaultContextMax
        const model = overrideModel !== undefined ? overrideModel : (session?.selected_model ?? store.defaultModel)
        return this.isContextMaxAutoPromoted(session, baseValue, model) ? CONTEXT_MAX.EXTENDED : baseValue
    }

    /**
     * If ``selectedModel`` is retired (past retirement date and not the
     * latest), return the next-higher version in the same family. Otherwise
     * ``null``. Used at render/send time to correct stale session settings.
     */
    // ─── Popover/summary rendering hooks ────────────────────────────────

    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') {
            const entry = this.getModelRegistry().find(e => e.selected_model === value)
            if (entry?.latest) return `${this.getModelLabel(value)} (latest: ${entry.version})`
            return this.getModelLabel(value)
        }
        return super.getDefaultValueLabel(field, value)
    }

    isChoiceDisabled(field, choiceValue, context) {
        if (field === 'effort') {
            if (choiceValue === EFFORT.X_HIGH) return !this.modelSupportsEffortXhigh(context?.effectiveModel)
            if (choiceValue === EFFORT.MAX) return !this.modelSupportsEffortMax(context?.effectiveModel)
        }
        return false
    }

    isFieldDisabled(field, context) {
        if (field === 'context_max') {
            if (context?.isStarting) return true
            if (context?.isContextMaxForced) return true
            if (!this.modelSupports1m(context?.effectiveModel)) return true
            return false
        }
        if (field === 'fast_mode') {
            if (!this.modelSupportsFast(context?.effectiveModel)) return true
            return false
        }
        return super.isFieldDisabled(field, context)
    }

    getFieldHelpText(field, context) {
        if (field === 'context_max') {
            if (context?.isContextMaxForced) return 'Forced to 1M: context usage exceeds 85% of 200K.'
            if (!this.modelSupports1m(context?.effectiveModel)) return '1M not available for this model version.'
            return null
        }
        if (field === 'fast_mode') {
            if (!this.modelSupportsFast(context?.effectiveModel)) return 'Fast mode is only available on supported Opus versions.'
            // Surface the cost note whenever the toggle is available — both in the
            // session popover and in the per-provider settings panel (the session-
            // level value here covers the popover; the provider panel synthesises
            // its own context with the default model).
            return 'Fast mode is billed separately from your subscription (extra usage credits).'
        }
        return null
    }

    getDisplayedSelectValue(field, selectedValue, context) {
        if (field === 'context_max' && context?.isContextMaxForced) {
            return String(CONTEXT_MAX.EXTENDED)
        }
        return super.getDisplayedSelectValue(field, selectedValue, context)
    }

    getSummaryParts(state) {
        const sel = state?.selected ?? {}
        const def = state?.defaults ?? {}
        const effectiveModel = sel.selected_model ?? def.selected_model
        const effectiveContextMax = sel.context_max ?? def.context_max
        const effectiveEffort = sel.effort ?? def.effort
        const effectiveThinking = sel.thinking_enabled ?? def.thinking_enabled
        const effectiveChrome = sel.claude_in_chrome ?? def.claude_in_chrome
        const effectiveFastMode = sel.fast_mode ?? def.fast_mode
        const effectivePermission = sel.permission_mode ?? def.permission_mode

        const modelLabel = this.getModelLabel(effectiveModel)
        const modelDisplay = effectiveContextMax === CONTEXT_MAX.EXTENDED
            ? `${modelLabel}[1m]`
            : modelLabel
        const modelForced = (sel.selected_model !== null && sel.selected_model !== undefined && sel.selected_model !== def.selected_model)
            || (sel.context_max !== null && sel.context_max !== undefined && sel.context_max !== def.context_max)

        const parts = [
            { text: modelDisplay, forced: modelForced },
            { text: this.getChoiceDisplayLabel('effort', effectiveEffort) ?? this.getChoiceLabel('effort', effectiveEffort) ?? '', forced: sel.effort !== null && sel.effort !== undefined && sel.effort !== def.effort },
            { text: this.getChoiceDisplayLabel('thinking_enabled', effectiveThinking) ?? this.getChoiceLabel('thinking_enabled', effectiveThinking) ?? '', forced: sel.thinking_enabled !== null && sel.thinking_enabled !== undefined && sel.thinking_enabled !== def.thinking_enabled },
            { text: this.getChoiceLabel('permission_mode', effectivePermission) ?? '', forced: sel.permission_mode !== null && sel.permission_mode !== undefined && sel.permission_mode !== def.permission_mode },
            { text: this.getChoiceDisplayLabel('claude_in_chrome', effectiveChrome) ?? this.getChoiceLabel('claude_in_chrome', effectiveChrome) ?? '', forced: sel.claude_in_chrome !== null && sel.claude_in_chrome !== undefined && sel.claude_in_chrome !== def.claude_in_chrome },
        ]
        if (effectiveFastMode) {
            parts.push({ text: 'Fast', forced: sel.fast_mode !== null && sel.fast_mode !== undefined && sel.fast_mode !== def.fast_mode })
        }
        return parts
    }

    getModelSelectGroups(registry) {
        const list = registry ?? []
        return [
            {
                entries: list.filter(e => e.latest).map(e => ({
                    value: e.selected_model,
                    label: `${this.getModelLabel(e.selected_model)} (latest: ${e.version})`,
                })),
            },
            {
                entries: list.filter(e => !e.latest).map(e => ({
                    value: e.selected_model,
                    label: `${this.getModelLabel(e.selected_model)} (until ${formatRetirementDate(e.retirement_date)})`,
                })),
            },
        ]
    }

    /**
     * Claude Code supports images, PDF, and plain-text uploads via the
     * SDK's content-block protocol. Images are resized at upload time to
     * the shared ``MAX_IMAGE_DIMENSION`` (2576 px, Opus 4.7's native
     * resolution — the most generous supported by any model we target),
     * not to Sonnet/Haiku's tighter 1568 px ceiling. The actual send-
     * time re-resize (down to 1568, 2000, or 2576 px) is decided per
     * (model, num_images) by ``MessageInput.handleSend`` so a stored
     * blob can serve any model without losing the option to use Opus
     * 4.7 at full resolution.
     */
    getAttachmentSupport() {
        return {
            images: true,
            documents: true,
            maxBytes: CLAUDE_MAX_FILE_BYTES,
            acceptedMimeTypes: [
                ...SUPPORTED_IMAGE_TYPES,
                ...SUPPORTED_DOCUMENT_TYPES,
                ...SUPPORTED_TEXT_TYPES,
            ],
            resizeImages: true,
        }
    }

    /**
     * Send-time long-edge cap for Claude. The rules track Anthropic's
     * published limits (see the vision docs):
     *
     *   - Models < Opus 4.7 (Sonnet / Haiku / older Opus): native
     *     resolution is 1568 px, so anything larger is downscaled
     *     server-side anyway. Resize client-side to save bandwidth
     *     and keep token usage predictable.
     *   - Opus 4.7+: native resolution is 2576 px (the storage
     *     dimension). Ship as-is.
     *   - Whenever the request carries more than 20 images, Anthropic
     *     caps every image at 2000 px regardless of model. Apply the
     *     tighter of {model cap, 2000} in that case.
     */
    getEffectiveImageDimension({ model, numImages } = {}) {
        const upgraded = this.getRetiredModelUpgrade(model) ?? model
        const isOpus47Plus = this._isOpus47Plus(upgraded)
        let cap = isOpus47Plus ? null : 1568
        if ((numImages ?? 0) > 20) {
            cap = cap === null ? 2000 : Math.min(cap, 2000)
        }
        return cap
    }

    /**
     * Whether ``selectedModel`` belongs to Opus 4.7 or any future major/
     * minor revision past it. Used by ``getEffectiveImageDimension`` to
     * grant images the full 2576 px storage resolution rather than
     * downscaling to 1568. We can't read this off the registry's
     * ``provider_extra`` directly (no flag yet for high-res image
     * support) so we parse the version string. Returns false on any
     * unparseable input — the conservative side falls back to 1568.
     */
    _isOpus47Plus(selectedModel) {
        if (!selectedModel || typeof selectedModel !== 'string') return false
        if (!selectedModel.startsWith('opus-')) return false
        const version = selectedModel.slice('opus-'.length)
        const [majorStr, minorStr] = version.split('.', 2)
        const major = Number(majorStr)
        const minor = Number(minorStr ?? '0')
        if (!Number.isFinite(major) || !Number.isFinite(minor)) return false
        if (major > 4) return true
        if (major < 4) return false
        return minor >= 7
    }

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
