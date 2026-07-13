import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER, SYNTHETIC_ITEM } from '../../constants'
import { getTwiccLaunchPrefix } from '../../utils/twiccLaunch'
import { SUPPORTED_IMAGE_TYPES } from '../../utils/fileUtils'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE, UNTRUSTED_PERMISSION_MODES } from './constants'
import { useCodexStore } from './store'

// TwiCC-handled hardcoded commands for Codex, invoked with ``/``. Unlike
// Claude Code's built-ins (interpreted by the CLI once the raw text reaches
// it), these are captured and executed entirely on the backend — the Codex
// CLI gives ``/`` no native meaning. Display-only here: this list feeds the
// ``/`` autocomplete; the backend owns capture (``agent/hardcoded_commands.py``)
// and execution (``CodexAgent``). ``$`` stays reserved for skills.
//
// ``is_builtin`` is the frontend-only picker sentinel (renders ``(built-in)``);
// no matching ``Command`` row exists, same as Claude Code. ``compact`` takes
// no argument on Codex (``argument_hint: null``) — the SDK ``thread_compact``
// accepts only the thread id. ``goal`` sets/clears the thread's Codex goal via
// the ``thread/goal/*`` app-server RPCs: ``/goal <objective>`` to set, ``/goal
// clear`` to remove it.
const BUILTIN_COMMANDS = [
    { name: 'compact', plugin_name: null, is_builtin: true, is_global: true, description: 'Compact the conversation context into a summary', argument_hint: null },
    { name: 'goal', plugin_name: null, is_builtin: true, is_global: true, description: "Set the session's goal (the objective Codex works toward), or 'clear' to remove it", argument_hint: '<objective> | clear' },
]

// Per-file ceiling for Codex uploads (5 MB). Aligned with the Claude
// per-image API limit so a draft built up under one provider can be
// switched to the other without a single attachment becoming invalid
// retroactively. Codex's own server-side resize (to 2048 px on the long
// edge) then handles whatever ends up reaching the CLI.
const CODEX_MAX_FILE_BYTES = 5 * 1024 * 1024

// Map of agent-setting wire names → store getter/setter for the persisted
// default. Used by ``getDefaultValue`` / ``setDefaultValue`` so generic
// surfaces (palette, settings popover) can read/write defaults without
// knowing the field-specific store property names.
const FIELD_TO_DEFAULT_STORE_BINDING = {
    selected_model:  { getter: 'defaultModel',          setter: 'setDefaultModel' },
    effort:          { getter: 'defaultEffort',         setter: 'setDefaultEffort' },
    permission_mode: { getter: 'defaultPermissionMode', setter: 'setDefaultPermissionMode' },
    permission_mode_if_untrusted: { getter: 'defaultUntrustedPermissionMode', setter: 'setDefaultUntrustedPermissionMode' },
    context_max:     { getter: 'defaultContextMax',     setter: 'setDefaultContextMax' },
}

// Map of synced setting keys (the wire/storage names) → store action that
// applies the value. Used by both ``applySyncedSettings`` (input) and
// ``getSyncedSettings`` (output) so the two sides can never drift apart.
const SYNCED_SETTING_KEYS_TO_STORE = {
    codexDefaultModel:          { setter: 'setDefaultModel',          getter: 'defaultModel' },
    codexDefaultEffort:         { setter: 'setDefaultEffort',         getter: 'defaultEffort' },
    codexDefaultPermissionMode: { setter: 'setDefaultPermissionMode', getter: 'defaultPermissionMode' },
    codexDefaultUntrustedPermissionMode: { setter: 'setDefaultUntrustedPermissionMode', getter: 'defaultUntrustedPermissionMode' },
    codexDefaultContextMax:     { setter: 'setDefaultContextMax',     getter: 'defaultContextMax' },
    codexUsageReadFileEnabled:  { setter: 'setUsageReadFileEnabled',  getter: 'usageReadFileEnabled' },
    codexUsageReadFilePath:     { setter: 'setUsageReadFilePath',     getter: 'usageReadFilePath' },
    codexUsageDumpFileEnabled:  { setter: 'setUsageDumpFileEnabled',  getter: 'usageDumpFileEnabled' },
    codexUsageDumpFilePath:     { setter: 'setUsageDumpFilePath',     getter: 'usageDumpFilePath' },
    codexQuotaWakeupTime:       { setter: 'setQuotaWakeupTime',       getter: 'quotaWakeupTime' },
}

// Map of usage-file field names (cross-provider) → Codex store
// getter/setter. Lets the generic ``getUsageFileSetting`` /
// ``setUsageFileSetting`` hooks proxy to this provider's own
// ``useCodexStore`` refs, so the on-disk shape and synced settings stay
// Codex-specific while the Settings UI stays provider-agnostic.
const USAGE_FILE_FIELD_TO_STORE_BINDING = {
    read_enabled: { getter: 'usageReadFileEnabled', setter: 'setUsageReadFileEnabled' },
    read_path:    { getter: 'usageReadFilePath',    setter: 'setUsageReadFilePath' },
    dump_enabled: { getter: 'usageDumpFileEnabled', setter: 'setUsageDumpFileEnabled' },
    dump_path:    { getter: 'usageDumpFilePath',    setter: 'setUsageDumpFilePath' },
}

// Statuspage display map: Statuspage v2 status values → footer rendering.
// Matches the status strings broadcast by the backend statuspage task.
const OPENAI_STATUS_DISPLAY = {
    operational:          { label: 'Operational',      modifier: 'ok' },
    degraded_performance: { label: 'Degraded',         modifier: 'warning' },
    partial_outage:       { label: 'Partial outage',   modifier: 'warning' },
    major_outage:         { label: 'Major outage',     modifier: 'error' },
    under_maintenance:    { label: 'Maintenance',      modifier: 'info' },
}

// Per-field choice catalogue for the Codex provider.
// ``selected_model`` is intentionally absent: the model list is served via
// the model registry (see ``getModelRegistry``).
const AGENT_SETTINGS_CHOICES = {
    permission_mode: [
        {
            value: PERMISSION_MODE.READ_ONLY,
            label: 'Read-only',
            description: 'Read-only. Any write requires confirmation.',
        },
        {
            value: PERMISSION_MODE.STRICT,
            label: 'Strict',
            description: 'Read-only. Writes are refused silently (no prompt).',
        },
        {
            value: PERMISSION_MODE.AUTO,
            label: 'Auto',
            description: 'Writes freely in the project; asks to step outside.',
        },
        {
            value: PERMISSION_MODE.AUTONOMOUS,
            label: 'Autonomous',
            description: 'Like Auto but uninterrupted (sandbox protects).',
        },
        {
            value: PERMISSION_MODE.YOLO,
            label: 'YOLO',
            description: 'No restrictions.',
        },
    ],
    effort: [
        { value: EFFORT.LOW,    label: 'Low',    display_label: 'Low effort' },
        { value: EFFORT.MEDIUM, label: 'Medium', display_label: 'Medium effort' },
        { value: EFFORT.HIGH,   label: 'High',   display_label: 'High effort' },
        { value: EFFORT.X_HIGH, label: 'xHigh',  display_label: 'xHigh effort' },
        { value: EFFORT.MAX,    label: 'Max',    display_label: 'Max effort' },
        { value: EFFORT.ULTRA,  label: 'Ultra',  display_label: 'Ultra effort' },
    ],
    // Not a user choice: the window is fixed by the model (272K pre-5.6,
    // 372K for the GPT-5.6 tiers) — the non-matching option is disabled and
    // ``enforceAgentSettingsConsistency`` pins the value to the model's.
    context_max: [
        { value: CONTEXT_MAX.DEFAULT, label: '272K' },
        { value: CONTEXT_MAX.LARGE, label: '372K' },
    ],
}

export class CodexHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CODEX
    static label = 'Codex'
    static icon = 'openai'

    canSendMessage() {
        return true
    }

    canStopSubagent() {
        // Codex exposes a ``close_agent`` LLM tool the parent model can
        // call autonomously, but no host-callable RPC to terminate a
        // spawned subagent (``turn/interrupt`` only stops a single
        // turn, doesn't shut the agent down, and needs a turn id we
        // don't track). Until that backend ``stop_subagent`` plumbing
        // exists, hide the Stop buttons (tool card + subagent header)
        // so the UI doesn't dispatch requests the backend would drop.
        return false
    }

    canInterruptTurn() {
        // Codex's ``turn/interrupt`` aborts the active turn and leaves the
        // thread alive (back to USER_TURN) — wired via CodexAgent.soft_interrupt.
        return true
    }

    getCommandActivationChars() {
        // Two prefixes for Codex: ``/`` for TwiCC's hardcoded commands
        // (captured + executed on the backend — the Codex CLI has no native
        // slash vocabulary) surfaced via ``getBuiltInCommands`` below, and
        // ``$`` for the skill catalogue (rows synced into the ``Command``
        // table by ``commands_task``; system/admin skills carry
        // ``is_builtin=true`` from the backend rather than a frontend constant).
        return ['/', '$']
    }

    getBuiltInCommands(activationChar) {
        // ``/`` surfaces TwiCC's hardcoded commands (compact, …); ``$`` is
        // skills-only (served from the backend ``Command`` table, no frontend
        // constant). See ``BUILTIN_COMMANDS`` above and the backend
        // ``agent/hardcoded_commands.py``.
        return activationChar === '/' ? BUILTIN_COMMANDS : []
    }

    buildOptimisticUserMessageContent(text, attachments) {
        // Codex JSONL shape for user input: ``{ type: 'event_msg', payload:
        // { type: 'user_message', message: '...', images: [...] } }``.
        // ``payload.message`` is the flat text; attached images live in
        // ``payload.images`` as full ``data:`` URLs (one per attachment).
        //
        // The frontend ships images through the Claude-shaped block format
        // (``{ type, source: { type: 'base64', media_type, data } }``), so
        // we re-pack each block into the ``data:`` URL the renderer will
        // see once the real JSONL line lands — that way ``Message.vue``
        // and ``UserMessage.vue`` run the exact same code path on both
        // the optimistic placeholder and the persisted item, and the
        // visual-item stabilizer can swap one for the other without
        // remounting the thumbnail group. ``documents`` are dropped:
        // Codex has no protocol equivalent (the frontend's capability
        // gating refuses them at attach time anyway, this is just
        // defensive).
        const images = (attachments?.images ?? [])
            .filter(block => block.source?.type === 'base64' && block.source?.data)
            .map(block => `data:${block.source.media_type || 'image/png'};base64,${block.source.data}`)
        return {
            type: 'event_msg',
            syntheticKind: SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind,
            payload: { type: 'user_message', message: text, images },
        }
    }

    extractUserMessageText(parsed) {
        if (parsed?.payload?.type !== 'user_message') return null
        if (typeof parsed.payload.message !== 'string') return null
        return parsed.payload.message.trim() || null
    }

    // ─── Authentication ─────────────────────────────────────────────────

    getAuthState() {
        return () => useCodexStore().authenticated
    }

    getAuthLoginCommand() {
        return `${getTwiccLaunchPrefix()} codex login`
    }

    async requestAuthRecheck() {
        // Lazy import to break the cycle between helpers and ws (ws imports
        // useWebSocket, which depends on providers/index, which imports
        // helpers).
        const { sendCheckAuth } = await import('./ws')
        sendCheckAuth()
    }

    getUntrustedPermissionModes() {
        return UNTRUSTED_PERMISSION_MODES
    }

    getDefaultValue(field) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return null
        return useCodexStore()[binding.getter]
    }

    setDefaultValue(field, value) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return
        useCodexStore()[binding.setter](value)
    }

    getSyncedSettingsKeys() {
        return Object.keys(SYNCED_SETTING_KEYS_TO_STORE)
    }

    applySyncedSettings(settings) {
        if (!settings || typeof settings !== 'object') return
        const store = useCodexStore()
        for (const [key, { setter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            if (key in settings) store[setter](settings[key])
        }
    }

    getSyncedSettings() {
        const store = useCodexStore()
        const result = {}
        for (const [key, { getter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            result[key] = store[getter]
        }
        return result
    }

    getAgentSettingsCategories() {
        return useCodexStore().agentSettingsCategories
    }

    getAgentSettingsChoices() {
        return AGENT_SETTINGS_CHOICES
    }

    /**
     * Effective context window for a Codex session.
     *
     * The window is a fixed per-model property (272K pre-5.6, 372K for the
     * GPT-5.6 tiers — the registry's ``provider_extra.context_window``), so
     * when the session names a model the registry knows, that window wins
     * over the persisted ``session.context_max``: rows written before the
     * per-model split (or by an out-of-date client) may carry the other
     * bucket, and the ring must reflect what Codex actually runs.
     *
     * The persisted value is the first fallback — it covers sessions whose
     * ``selected_model`` is null (imported JSONL rollouts, where the compute
     * pipeline derived ``context_max`` from ``task_started`` and it is
     * therefore trustworthy). The default model's window, then the
     * hard-coded ``CONTEXT_MAX.DEFAULT``, close the chain so the progress
     * ring never divides by ``null``.
     */
    getEffectiveContextMax(session, overrideModel = undefined) {
        const store = useCodexStore()
        const model = overrideModel !== undefined ? overrideModel : session?.selected_model
        if (model) {
            const entry = store.modelRegistry.find(e => e.selected_model === model)
            const windowSize = entry?.provider_extra?.context_window
            if (windowSize) return windowSize
        }
        return session?.context_max ?? this.modelContextWindow(null) ?? CONTEXT_MAX.DEFAULT
    }

    /**
     * Build a human-friendly label for a Codex ``selected_model`` value.
     * "gpt" → "GPT", "gpt-5.5" → "GPT 5.5".
     */
    getModelLabel(selectedModel) {
        if (!selectedModel) return ''
        if (selectedModel.includes('-')) {
            const [model, version] = selectedModel.split('-', 2)
            return `${model.toUpperCase()} ${version}`
        }
        return selectedModel.toUpperCase()
    }

    /**
     * Short grid-row label: drop the "gpt-" prefix so "gpt-sol" → "Sol",
     * "gpt-mini" → "Mini"; the bare "gpt" family stays "GPT". Numeric-suffixed
     * legacy aliases ("gpt-5.4") keep the full "GPT 5.4" label to stay clear.
     */
    getModelShortLabel(selectedModel) {
        if (!selectedModel) return ''
        if (selectedModel === 'gpt') return 'GPT'
        const suffix = selectedModel.startsWith('gpt-') ? selectedModel.slice(4) : selectedModel
        if (/^[a-z]+$/i.test(suffix)) {
            return suffix.charAt(0).toUpperCase() + suffix.slice(1)
        }
        return this.getModelLabel(selectedModel)
    }

    getModelRegistry() {
        return useCodexStore().modelRegistry
    }

    // ─── Model capabilities ──────────────────────────────────────────────
    // Mirrors the backend ``selected_model_supports_*`` helpers: when the
    // explicit ``selectedModel`` is unknown to the registry, fall back to the
    // current synced default model. The conservative last-resort answer is
    // ``false`` so an effort isn't advertised before the registry is seeded.

    _resolveRegistryEntry(selectedModel) {
        const store = useCodexStore()
        const registry = store.modelRegistry
        let entry = selectedModel ? registry.find(e => e.selected_model === selectedModel) : undefined
        if (!entry) {
            const defaultModel = store.defaultModel
            if (defaultModel) entry = registry.find(e => e.selected_model === defaultModel)
        }
        return entry
    }

    modelSupportsEffortMax(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? !!entry.provider_extra?.supports_effort_max : false
    }

    modelSupportsEffortUltra(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? !!entry.provider_extra?.supports_effort_ultra : false
    }

    /**
     * The model's fixed Codex input window (272K pre-5.6, 372K for the
     * GPT-5.6 tiers), from the registry's ``provider_extra.context_window``.
     * Falls back to the default model when ``selectedModel`` is unknown
     * (same convention as the effort capability checks); ``null`` when
     * nothing resolves (registry not seeded yet). Mirrors the backend
     * ``selected_model_context_window``.
     */
    modelContextWindow(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry?.provider_extra?.context_window ?? null
    }

    /**
     * Pipeline mirroring the backend ``CodexHelpers.enforce_agent_settings_consistency``:
     * substitute a retired model (``super``), demote ``ultra`` → ``max`` →
     * ``xhigh`` against the resolved model, then pin ``contextMax`` to the
     * model's fixed window (not a user choice on Codex — both directions).
     * Called by ``useSessionAgentSettings`` whenever the model or effort
     * changes, so the popover selection follows the model immediately instead
     * of waiting for the backend to correct it.
     */
    enforceAgentSettingsConsistency(settings) {
        const result = super.enforceAgentSettingsConsistency(settings)
        const model = result.selectedModel

        if (result.effort === EFFORT.ULTRA && !this.modelSupportsEffortUltra(model)) {
            result.effort = this.modelSupportsEffortMax(model) ? EFFORT.MAX : EFFORT.X_HIGH
        }
        if (result.effort === EFFORT.MAX && !this.modelSupportsEffortMax(model)) {
            result.effort = EFFORT.X_HIGH
        }

        const windowSize = this.modelContextWindow(model)
        if (result.contextMax != null && windowSize && result.contextMax !== windowSize) {
            result.contextMax = windowSize
        }
        return result
    }

    isChoiceDisabled(field, choiceValue, context) {
        if (super.isChoiceDisabled(field, choiceValue, context)) return true
        if (field === 'effort') {
            if (choiceValue === EFFORT.MAX) return !this.modelSupportsEffortMax(context?.effectiveModel)
            if (choiceValue === EFFORT.ULTRA) return !this.modelSupportsEffortUltra(context?.effectiveModel)
        }
        if (field === 'context_max') {
            const windowSize = this.modelContextWindow(context?.effectiveModel)
            if (windowSize) return choiceValue !== windowSize
        }
        return false
    }

    getChoiceDisabledReason(field, choiceValue, context) {
        if (field === 'context_max') {
            const windowSize = this.modelContextWindow(context?.effectiveModel)
            if (windowSize && choiceValue !== windowSize) {
                return 'The context window is fixed by the model.'
            }
        }
        return super.getChoiceDisabledReason(field, choiceValue, context)
    }

    getFieldHelpText(field, context) {
        if (field === 'context_max') {
            return super.getFieldHelpText(field, context)
                ?? 'Fixed by the model: 272K up to GPT 5.5, 372K for the 5.6 tiers.'
        }
        return super.getFieldHelpText(field, context)
    }

    // ─── Usage quota tracking ────────────────────────────────────────────

    tracksUsage() {
        return true
    }

    getUsageExternalLink() {
        return {
            url: 'https://chatgpt.com/codex/cloud/settings/analytics#usage',
            label: 'View usage on chatgpt.com',
        }
    }

    supportsUsageRefresh() {
        return true
    }

    async requestUsageRefresh() {
        // Lazy import to break the helpers ↔ ws cycle (see requestAuthRecheck).
        const { sendCheckUsage } = await import('./ws')
        sendCheckUsage()
    }

    getUsageFileSetting(field) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return null
        return useCodexStore()[binding.getter]
    }

    setUsageFileSetting(field, value) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return
        useCodexStore()[binding.setter](value)
    }

    supportsQuotaWakeup() {
        return true
    }

    getQuotaWakeupTime() {
        return useCodexStore().quotaWakeupTime || ''
    }

    setQuotaWakeupTime(value) {
        useCodexStore().setQuotaWakeupTime(value)
    }

    // ─── Service status (OpenAI statuspage) ──────────────────────────────

    getServiceStatus() {
        return () => useCodexStore().openaiStatus
    }

    getServiceStatusDisplay(status) {
        const entry = OPENAI_STATUS_DISPLAY[status] ?? { label: status, modifier: 'ok' }
        return {
            ...entry,
            url: 'https://status.openai.com/',
            tooltip: "Codex status on OpenAI's side",
        }
    }

    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') {
            const resolved = this.resolveToAvailableModel(value)
            const entry = this.getModelRegistry().find(e => e.selected_model === resolved)
            if (entry?.latest) return `${this.getModelLabel(resolved)} (latest: ${entry.version})`
            return this.getModelLabel(resolved)
        }
        return super.getDefaultValueLabel(field, value)
    }

    getModelSelectGroups(registry) {
        const list = registry ?? []
        return [
            {
                entries: list.filter(e => e.latest).map(e => this.buildModelOption(
                    e, `${this.getModelLabel(e.selected_model)} (latest: ${e.version})`,
                )),
            },
            {
                entries: list.filter(e => !e.latest).map(e => this.buildModelOption(
                    e, this.getModelLabel(e.selected_model),
                )),
            },
        ]
    }

    /**
     * Codex accepts images only. The Codex CLI core forwards
     * ``ImageInput.url`` as either an http(s) URL or a base64 data URL.
     * PDFs and text files have no input-block equivalent in the Codex
     * protocol and are intentionally excluded.
     *
     * Images are resized to the shared ``MAX_IMAGE_DIMENSION`` (2576 px,
     * Opus 4.7's native resolution) at upload time so the same stored
     * blob can be sent to any provider without re-encoding. Codex's own
     * server-side resize (to 2048 px) absorbs whatever ends up at the
     * CLI without further frontend work.
     */
    getAttachmentSupport() {
        return {
            images: true,
            documents: false,
            maxBytes: CODEX_MAX_FILE_BYTES,
            acceptedMimeTypes: [...SUPPORTED_IMAGE_TYPES],
            resizeImages: true,
        }
    }
}

export const codexHelpers = new CodexHelpers()
