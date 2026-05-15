import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER, SYNTHETIC_ITEM } from '../../constants'
import { useSettingsStore } from '../../stores/settings'
import { SUPPORTED_IMAGE_TYPES } from '../../utils/fileUtils'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'
import { useCodexStore } from './store'

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
    context_max:     { getter: 'defaultContextMax',     setter: 'setDefaultContextMax' },
}

// Map of synced setting keys (the wire/storage names) → store action that
// applies the value. Used by both ``applySyncedSettings`` (input) and
// ``getSyncedSettings`` (output) so the two sides can never drift apart.
const SYNCED_SETTING_KEYS_TO_STORE = {
    codexDefaultModel:          { setter: 'setDefaultModel',          getter: 'defaultModel' },
    codexDefaultEffort:         { setter: 'setDefaultEffort',         getter: 'defaultEffort' },
    codexDefaultPermissionMode: { setter: 'setDefaultPermissionMode', getter: 'defaultPermissionMode' },
    codexDefaultContextMax:     { setter: 'setDefaultContextMax',     getter: 'defaultContextMax' },
    codexUsageReadFileEnabled:  { setter: 'setUsageReadFileEnabled',  getter: 'usageReadFileEnabled' },
    codexUsageReadFilePath:     { setter: 'setUsageReadFilePath',     getter: 'usageReadFilePath' },
    codexUsageDumpFileEnabled:  { setter: 'setUsageDumpFileEnabled',  getter: 'usageDumpFileEnabled' },
    codexUsageDumpFilePath:     { setter: 'setUsageDumpFilePath',     getter: 'usageDumpFilePath' },
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
    ],
    context_max: [
        { value: CONTEXT_MAX.DEFAULT, label: '272K' },
    ],
}

export class CodexHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CODEX
    static label = 'Codex'

    canSendMessage() {
        return true
    }

    getCommandActivationChars() {
        // Codex exposes its skill catalogue under the ``$`` prefix; the
        // matching backend rows land in the ``Command`` table via the
        // ``commands_task`` skill-list sync. No ``getBuiltInCommands``
        // override needed — system/admin skills come back from the
        // backend with ``is_builtin=true`` rather than from a frontend
        // constant.
        return ['$']
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
        return `${useSettingsStore().twiccLaunchPrefix} codex login`
    }

    async requestAuthRecheck() {
        // Lazy import to break the cycle between helpers and ws (ws imports
        // useWebSocket, which depends on providers/index, which imports
        // helpers).
        const { sendCheckAuth } = await import('./ws')
        sendCheckAuth()
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
     * Codex CLI exposes a single context_max bucket today (272K, the
     * gpt-5 family window), so the override is just the standard
     * fallback chain: the persisted ``session.context_max`` first, then
     * the synced default the user chose in their global settings, then
     * the hard-coded ``CONTEXT_MAX.DEFAULT`` baked into Codex CLI.
     *
     * The last fallback is what makes this override matter in practice:
     * sessions imported from a JSONL file have ``context_max`` set to
     * ``NULL`` in the DB (the compute pipeline doesn't populate it —
     * Codex doesn't write the window into its JSONL anywhere we could
     * read it), and would otherwise surface as ``null`` in the
     * progress ring → ``Infinity%`` divide-by-zero in
     * ``SessionHeader.vue``.
     */
    getEffectiveContextMax(session) {
        const store = useCodexStore()
        return session?.context_max ?? store.defaultContextMax ?? CONTEXT_MAX.DEFAULT
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

    getModelRegistry() {
        return useCodexStore().modelRegistry
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
            const entry = this.getModelRegistry().find(e => e.selected_model === value)
            if (entry?.latest) return `${this.getModelLabel(value)} (latest: ${entry.version})`
            return this.getModelLabel(value)
        }
        return super.getDefaultValueLabel(field, value)
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
                    label: this.getModelLabel(e.selected_model),
                })),
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
