// frontend/src/providers/claude_code/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'

const SETTINGS_PRESET_FIELDS = [
    'model',
    'context_max',
    'effort',
    'thinking',
    'permission_mode',
    'claude_in_chrome',
]

function normalizeSettingsPreset(raw) {
    const preset = { name: typeof raw?.name === 'string' ? raw.name : '' }
    for (const field of SETTINGS_PRESET_FIELDS) {
        preset[field] = raw && field in raw ? raw[field] : null
    }
    return preset
}

export const useClaudeCodeStore = defineStore('claudeCode', () => {
    // ─── Auth ────────────────────────────────────────────────────────────

    // Claude CLI authentication state (from claude_code:auth_updated messages).
    // null = unknown (no message received yet), true/false = known state.
    // Driven by the backend's auth_task (periodic check) and on-connect push.
    const authenticated = ref(null)

    function setAuthenticated(value) {
        authenticated.value = value
    }

    // ─── Anthropic statuspage ────────────────────────────────────────────

    // Anthropic statuspage component status (from claude_code:anthropic_status
    // messages). Defaults to 'operational' so the UI doesn't flash a warning
    // before the first push arrives.
    const anthropicStatus = ref('operational')

    function setAnthropicStatus(value) {
        anthropicStatus.value = value
    }

    // ─── Usage quota ─────────────────────────────────────────────────────

    // Per-provider usage data. ``null`` until the bootstrap seed or the
    // first ``usage_updated`` push arrives, then ``{ success, reason, raw,
    // computed }``. Consumers should treat ``null`` as "not yet loaded".
    const usage = ref(null)

    function setUsage(success, reason, raw, computed) {
        usage.value = { success, reason, raw, computed }
    }

    // ─── Settings presets ────────────────────────────────────────────────

    const settingsPresets = ref([])
    const settingsPresetsInitialized = ref(false)

    function applySettingsPresetsConfig(config) {
        const list = Array.isArray(config?.presets) ? config.presets : []
        settingsPresets.value = list.map(normalizeSettingsPreset)
        settingsPresetsInitialized.value = true
    }

    async function _sendSettingsPresetsConfig() {
        // Lazy import to avoid the circular dep with the sibling ws.js module
        // (ws.js eagerly imports this store; if we eagerly imported ws.js the
        // module evaluation order would deadlock).
        const { sendUpdateSettingsPresets } = await import('./ws')
        sendUpdateSettingsPresets({ presets: settingsPresets.value })
    }

    function findSettingsPresetIndexByName(name, excludeIndex = null) {
        const target = name.trim().toLowerCase()
        return settingsPresets.value.findIndex((p, i) => i !== excludeIndex && p.name.trim().toLowerCase() === target)
    }

    function findSettingsPresetByName(name, excludeIndex = null) {
        const idx = findSettingsPresetIndexByName(name, excludeIndex)
        return idx === -1 ? null : settingsPresets.value[idx]
    }

    function addSettingsPreset(preset) {
        settingsPresets.value.push(normalizeSettingsPreset(preset))
        _sendSettingsPresetsConfig()
    }

    function updateSettingsPreset(index, preset) {
        if (index < 0 || index >= settingsPresets.value.length) return
        settingsPresets.value.splice(index, 1, normalizeSettingsPreset(preset))
        _sendSettingsPresetsConfig()
    }

    function deleteSettingsPreset(index) {
        if (index < 0 || index >= settingsPresets.value.length) return
        settingsPresets.value.splice(index, 1)
        _sendSettingsPresetsConfig()
    }

    function duplicateSettingsPreset(index) {
        if (index < 0 || index >= settingsPresets.value.length) return
        const source = settingsPresets.value[index]
        const baseName = `${source.name} (copy)`
        let candidate = baseName
        let n = 2
        while (findSettingsPresetIndexByName(candidate) !== -1) {
            candidate = `${baseName} ${n}`
            n += 1
        }
        const copy = normalizeSettingsPreset({ ...source, name: candidate })
        settingsPresets.value.splice(index + 1, 0, copy)
        _sendSettingsPresetsConfig()
    }

    function reorderSettingsPreset(index, direction) {
        const target = index + direction
        if (target < 0 || target >= settingsPresets.value.length) return
        const [moved] = settingsPresets.value.splice(index, 1)
        settingsPresets.value.splice(target, 0, moved)
        _sendSettingsPresetsConfig()
    }

    // ─── Per-session agent settings defaults ─────────────────────────────
    //
    // Default values applied to new Claude Code sessions and used as the
    // fallback for the dropdowns in the message input. Persisted to
    // localStorage and synchronized to the backend via the synced settings
    // orchestrator (the keys are declared by ``ClaudeCodeHelpers.getSyncedSettingsKeys``
    // so the orchestrator dispatches incoming/outgoing values here).

    const defaultPermissionMode = ref(null)
    const defaultModel = ref(null)
    const defaultContextMax = ref(null)
    const defaultEffort = ref(null)
    const defaultThinking = ref(null)
    const defaultClaudeInChrome = ref(null)

    function setDefaultPermissionMode(value) {
        if (Object.values(PERMISSION_MODE).includes(value)) defaultPermissionMode.value = value
    }
    function setDefaultModel(value) {
        if (typeof value === 'string' && value.length > 0) defaultModel.value = value
    }
    function setDefaultContextMax(value) {
        if (Object.values(CONTEXT_MAX).includes(value)) defaultContextMax.value = value
    }
    function setDefaultEffort(value) {
        if (Object.values(EFFORT).includes(value)) defaultEffort.value = value
    }
    function setDefaultThinking(value) {
        if (typeof value === 'boolean') defaultThinking.value = value
    }
    function setDefaultClaudeInChrome(value) {
        if (typeof value === 'boolean') defaultClaudeInChrome.value = value
    }

    // ─── Agent settings categories (live/idle/startup) ───────────────────
    //
    // Classification of the per-session agent setting fields by lifecycle
    // applicability, mirroring the backend ``BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES``.
    // Seeded from the bootstrap payload ``providers.claude_code.agent_settings_categories``
    // and consumed via ``ClaudeCodeHelpers.classifyAgentSettingsChanges``.

    const agentSettingsCategories = ref({ live: [], idle: [], startup: [] })

    function setAgentSettingsCategories(value) {
        if (!value || typeof value !== 'object') return
        agentSettingsCategories.value = {
            live: Array.isArray(value.live) ? value.live : [],
            idle: Array.isArray(value.idle) ? value.idle : [],
            startup: Array.isArray(value.startup) ? value.startup : [],
        }
    }

    // ─── Model registry ──────────────────────────────────────────────────
    //
    // List of models exposed by the Claude Code provider, seeded from the
    // bootstrap payload ``providers.claude_code.model_registry``. Each entry
    // carries selection metadata (``selected_model``, ``model``, ``version``,
    // ``latest``, ``retirement_date``) and capability flags under
    // ``provider_extra`` (``supports_1m``, ``supports_effort_xhigh``,
    // ``supports_effort_max``). Consumed via ``ClaudeCodeHelpers``' capability
    // and retired-model methods.

    const modelRegistry = ref([])

    function setModelRegistry(registry) {
        modelRegistry.value = Array.isArray(registry) ? registry : []
    }

    return {
        authenticated,
        setAuthenticated,
        anthropicStatus,
        setAnthropicStatus,
        usage,
        setUsage,
        settingsPresets,
        settingsPresetsInitialized,
        applySettingsPresetsConfig,
        findSettingsPresetByName,
        findSettingsPresetIndexByName,
        addSettingsPreset,
        updateSettingsPreset,
        deleteSettingsPreset,
        duplicateSettingsPreset,
        reorderSettingsPreset,
        defaultPermissionMode,
        defaultModel,
        defaultContextMax,
        defaultEffort,
        defaultThinking,
        defaultClaudeInChrome,
        setDefaultPermissionMode,
        setDefaultModel,
        setDefaultContextMax,
        setDefaultEffort,
        setDefaultThinking,
        setDefaultClaudeInChrome,
        agentSettingsCategories,
        setAgentSettingsCategories,
        modelRegistry,
        setModelRegistry,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useClaudeCodeStore, import.meta.hot))
}
