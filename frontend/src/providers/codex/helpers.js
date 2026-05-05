import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER } from '../../constants'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'
import { useCodexStore } from './store'

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
            value: PERMISSION_MODE.AUTO,
            label: 'Auto',
            description: 'Writes freely in the workspace; asks to step outside.',
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

    // No agent runtime yet — the send button stays grayed out for any
    // session whose provider is Codex.
    canSendMessage() {
        return false
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
}

export const codexHelpers = new CodexHelpers()
