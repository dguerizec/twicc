// frontend/src/providers/claude_code/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'

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
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useClaudeCodeStore, import.meta.hot))
}
