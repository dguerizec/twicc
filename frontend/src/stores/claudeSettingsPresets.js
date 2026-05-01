import { defineStore } from 'pinia'
import { ref } from 'vue'

const PRESET_FIELDS = [
    'model',
    'context_max',
    'effort',
    'thinking',
    'permission_mode',
    'claude_in_chrome',
]

function normalizePreset(raw) {
    const preset = { name: typeof raw?.name === 'string' ? raw.name : '' }
    for (const field of PRESET_FIELDS) {
        preset[field] = raw && field in raw ? raw[field] : null
    }
    return preset
}

export const useClaudeSettingsPresetsStore = defineStore('claudeSettingsPresets', () => {
    const presets = ref([])
    const initialized = ref(false)

    function applyConfig(config) {
        const list = Array.isArray(config?.presets) ? config.presets : []
        presets.value = list.map(normalizePreset)
        initialized.value = true
    }

    async function _sendConfig() {
        const { sendUpdateSettingsPresets } = await import('../providers/claude_code/ws')
        sendUpdateSettingsPresets({ presets: presets.value })
    }

    function findPresetIndexByName(name, excludeIndex = null) {
        const target = name.trim().toLowerCase()
        return presets.value.findIndex((p, i) => i !== excludeIndex && p.name.trim().toLowerCase() === target)
    }

    function findPresetByName(name, excludeIndex = null) {
        const idx = findPresetIndexByName(name, excludeIndex)
        return idx === -1 ? null : presets.value[idx]
    }

    function addPreset(preset) {
        presets.value.push(normalizePreset(preset))
        _sendConfig()
    }

    function updatePreset(index, preset) {
        if (index < 0 || index >= presets.value.length) return
        presets.value.splice(index, 1, normalizePreset(preset))
        _sendConfig()
    }

    function deletePreset(index) {
        if (index < 0 || index >= presets.value.length) return
        presets.value.splice(index, 1)
        _sendConfig()
    }

    function duplicatePreset(index) {
        if (index < 0 || index >= presets.value.length) return
        const source = presets.value[index]
        const baseName = `${source.name} (copy)`
        let candidate = baseName
        let n = 2
        while (findPresetIndexByName(candidate) !== -1) {
            candidate = `${baseName} ${n}`
            n += 1
        }
        const copy = normalizePreset({ ...source, name: candidate })
        presets.value.splice(index + 1, 0, copy)
        _sendConfig()
    }

    function reorderPreset(index, direction) {
        const target = index + direction
        if (target < 0 || target >= presets.value.length) return
        const [moved] = presets.value.splice(index, 1)
        presets.value.splice(target, 0, moved)
        _sendConfig()
    }

    return {
        presets,
        initialized,
        applyConfig,
        findPresetByName,
        findPresetIndexByName,
        addPreset,
        updatePreset,
        deletePreset,
        duplicatePreset,
        reorderPreset,
    }
})
