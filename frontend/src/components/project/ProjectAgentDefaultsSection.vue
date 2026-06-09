<script setup>
// Per-project agent settings defaults, edited inside ProjectEditDialog (edit
// mode only). Lets a project declare a default provider + per-provider agent
// settings bundles that seed a new session's settings, inherited up the project
// hierarchy (resolution mirrored in utils/projectAgentDefaults.js; the backend
// re-resolves the same chain). NULL/absent fields inherit from parent projects,
// then the global defaults.
//
// Tabs cover ALL registered providers — including currently-disabled ones, since
// a deactivation may be temporary and the project should keep its defaults.
//
// Field rows reuse the same provider rendering hooks as AgentSettingsPopover /
// AgentSettingsPresetsDialog (getFieldLabel / getFieldChoices /
// getModelSelectGroups), so a new provider needs zero template changes here.
//
// This component owns its local draft state and exposes getChangedFields() to
// the parent, which folds the result into its existing PUT /api/projects/<id>/.

import { ref, computed, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers, getProviderStore, getProviderOptions, getProviderLabel, getProviderIcon } from '../../providers'
import { useAgentSettingsPresetsStore } from '../../stores/agentSettingsPresets'
import { ancestorChain } from '../../utils/projectAgentDefaults'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'

const props = defineProps({
    project: { type: Object, required: true },
})

const store = useDataStore()
const settingsStore = useSettingsStore()
const presetsStore = useAgentSettingsPresetsStore()

// Canonical wire field names (the same order/shape as the preset form).
const FIELD_ORDER = ['selected_model', 'context_max', 'effort', 'thinking_enabled', 'permission_mode', 'claude_in_chrome', 'fast_mode']
// Preset records use historical key names; default_agent_settings uses wire names.
const PRESET_TO_WIRE = {
    model: 'selected_model',
    context_max: 'context_max',
    effort: 'effort',
    thinking: 'thinking_enabled',
    permission_mode: 'permission_mode',
    claude_in_chrome: 'claude_in_chrome',
    fast_mode: 'fast_mode',
}

// All registered providers (enabled + disabled).
const providers = computed(() => getProviderOptions())
const enabledProviders = computed(() => new Set(settingsStore.enabledProviders))

// ─── Local draft state ───────────────────────────────────────────────────────
// localProvider: '' = inherit, else a provider value.
// localSettings: { <provider>: { <wire field>: value } } — sparse (absent = inherit).
const localProvider = ref('')
const localSettings = ref({})
const activeTab = ref('')

function initLocal() {
    localProvider.value = props.project?.default_provider ?? ''
    localSettings.value = JSON.parse(JSON.stringify(props.project?.default_agent_settings ?? {}))
    // Open on the project's default provider when it's enabled, else the first
    // enabled provider — no point landing on a disabled tab. Fall back to the
    // very first provider only if every provider is disabled.
    const preferred = props.project?.default_provider
    activeTab.value = (preferred && isEnabled(preferred))
        ? preferred
        : (providers.value.find(p => isEnabled(p.value))?.value ?? providers.value[0]?.value ?? '')
}
watch(() => props.project, initLocal, { immediate: true })

// ─── Inherited default provider (shown when the choice is "inherit") ──────────
const inheritedProvider = computed(() => {
    for (const node of ancestorChain(props.project.id, store.projects)) {
        if (node.id === props.project.id) continue  // skip self — we show the fallback
        if (node.default_provider) return node.default_provider
    }
    return settingsStore.defaultProvider || null
})
const inheritedProviderLabel = computed(() =>
    inheritedProvider.value ? getProviderLabel(inheritedProvider.value) : 'none'
)

// ─── Per-provider field helpers ──────────────────────────────────────────────
function helpersFor(provider) {
    return getProviderHelpers(provider)
}
function supportedFields(provider) {
    const h = helpersFor(provider)
    if (!h) return []
    return FIELD_ORDER.filter(f => h.supportsAgentSetting(f))
}
function modelGroups(provider) {
    const h = helpersFor(provider)
    if (!h) return []
    return h.getModelSelectGroups(h.getModelRegistry())
}
function fieldLabel(provider, field) {
    return helpersFor(provider)?.getFieldLabel(field) ?? field
}
function fieldChoices(provider, field) {
    return helpersFor(provider)?.getFieldChoices(field) ?? []
}
function fieldValue(provider, field) {
    return localSettings.value[provider]?.[field] ?? null
}
function isEnabled(provider) {
    return enabledProviders.value.has(provider)
}

function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}
function fromSentinel(provider, field, raw) {
    if (raw === DEFAULT_SENTINEL) return null
    if (field === 'selected_model') return raw
    const match = fieldChoices(provider, field).find(opt => String(opt.value) === raw)
    return match ? match.value : raw
}

// Immutable nested update so reactivity tracks the change; null = inherit, which
// drops the key to keep the stored bundle sparse.
function setField(provider, field, rawValue) {
    const value = fromSentinel(provider, field, rawValue)
    const next = { ...(localSettings.value[provider] || {}) }
    if (value === null) delete next[field]
    else next[field] = value
    localSettings.value = { ...localSettings.value, [provider]: next }
}

// ─── "Load from…" (named presets + ancestor projects' bundles) ────────────────
function loadSources(provider) {
    const sources = []
    // Ancestor projects that define their own defaults for this provider.
    for (const node of ancestorChain(props.project.id, store.projects)) {
        if (node.id === props.project.id) continue
        const bundle = node.default_agent_settings?.[provider]
        if (bundle && Object.keys(bundle).length) {
            const name = node.name || node.directory || node.id
            sources.push({ key: `ancestor:${node.id}`, label: `Project: ${name}` })
        }
    }
    // The provider's global (synced) defaults.
    sources.push({ key: 'global', label: 'Global defaults' })
    // Named presets.
    const presets = presetsStore.getPresets(provider) || []
    presets.forEach((preset, index) => {
        sources.push({ key: `preset:${index}`, label: `Preset: ${preset.name}` })
    })
    return sources
}
function presetToBundle(preset) {
    const bundle = {}
    for (const [presetKey, wire] of Object.entries(PRESET_TO_WIRE)) {
        if (preset[presetKey] != null) bundle[wire] = preset[presetKey]
    }
    return bundle
}
// The provider's global (synced) defaults as a wire-named bundle.
function globalBundleFor(provider) {
    const s = getProviderStore(provider)
    if (!s) return {}
    return {
        selected_model: s.defaultModel,
        context_max: s.defaultContextMax,
        effort: s.defaultEffort,
        thinking_enabled: s.defaultThinking,
        permission_mode: s.defaultPermissionMode,
        claude_in_chrome: s.defaultClaudeInChrome,
        fast_mode: s.defaultFastMode,
    }
}
function loadFrom(provider, event) {
    const key = event.target.value
    event.target.value = ''  // reset back to the placeholder
    if (!key) return
    let bundle = {}
    if (key.startsWith('preset:')) {
        const index = Number(key.slice('preset:'.length))
        const preset = (presetsStore.getPresets(provider) || [])[index]
        if (!preset) return
        bundle = presetToBundle(preset)
    } else if (key.startsWith('ancestor:')) {
        const nodeId = key.slice('ancestor:'.length)
        bundle = { ...(store.getProject(nodeId)?.default_agent_settings?.[provider] || {}) }
    } else if (key === 'global') {
        bundle = globalBundleFor(provider)
    }
    const clean = {}
    for (const field of FIELD_ORDER) {
        if (bundle[field] != null) clean[field] = bundle[field]
    }
    localSettings.value = { ...localSettings.value, [provider]: clean }
}

// ─── Exposed to the parent dialog ────────────────────────────────────────────
// Normalize to sparse storage (drop null fields + empty bundles → null), so
// comparisons and what we send match the backend's own normalization.
function normalizeSettings(raw) {
    const out = {}
    for (const provider of Object.keys(raw || {})) {
        const bundle = {}
        for (const field of FIELD_ORDER) {
            if (raw[provider]?.[field] != null) bundle[field] = raw[provider][field]
        }
        if (Object.keys(bundle).length) out[provider] = bundle
    }
    return Object.keys(out).length ? out : null
}

// Returns only the fields that actually changed vs the project, so the parent's
// PUT leaves untouched fields alone (the backend only updates keys it receives).
function getChangedFields() {
    const changed = {}
    const newProvider = localProvider.value || null
    if (newProvider !== (props.project.default_provider ?? null)) {
        changed.default_provider = newProvider
    }
    const newSettings = normalizeSettings(localSettings.value)
    const oldSettings = normalizeSettings(props.project.default_agent_settings)
    if (JSON.stringify(newSettings) !== JSON.stringify(oldSettings)) {
        changed.default_agent_settings = newSettings
    }
    return changed
}

defineExpose({ getChangedFields, reset: initLocal })
</script>

<template>
    <div class="form-group">
        <label class="form-label">Default provider</label>
        <p class="ad-intro">
            Which agent a new session in this project starts with. Inherits from
            parent projects, then your global default.
        </p>
        <wa-select
            :value.prop="localProvider"
            @change="localProvider = $event.target.value"
            size="small"
            class="ad-provider-select"
        >
            <wa-icon
                v-if="localProvider && getProviderIcon(localProvider)"
                slot="start"
                auto-width
                family="brands"
                :name="getProviderIcon(localProvider)"
            ></wa-icon>
            <wa-option value="" label="Inherit">Inherit</wa-option>
            <wa-option
                v-for="p in providers"
                :key="p.value"
                :value="p.value"
                :label="isEnabled(p.value) ? getProviderLabel(p.value) : getProviderLabel(p.value) + ' (disabled)'"
            >
                <wa-icon
                    v-if="getProviderIcon(p.value)"
                    auto-width
                    family="brands"
                    :name="getProviderIcon(p.value)"
                    class="provider-option-icon"
                ></wa-icon>
                {{ getProviderLabel(p.value) }}<template v-if="!isEnabled(p.value)"> (disabled)</template>
            </wa-option>
        </wa-select>
        <div v-if="localProvider === ''" class="form-hint">
            Inherited: <strong>{{ inheritedProviderLabel }}</strong>
        </div>
    </div>

    <div class="form-group">
        <label class="form-label">Agent settings defaults</label>
        <p class="ad-intro">
            Per-provider defaults for new sessions here. Empty fields inherit from
            parent projects, then your global defaults.
        </p>
        <!-- .stop on the tab events so our per-provider tabs never bubble up to
             the dialog's main "Project / Agent settings" tab-group; the handler
             keeps activeTab in sync so the controlled :active never snaps back. -->
        <wa-tab-group
            :active="activeTab"
            @wa-tab-show.stop="activeTab = $event.detail?.name ?? activeTab"
            @wa-tab-hide.stop
        >
            <wa-tab
                v-for="p in providers"
                :key="p.value"
                slot="nav"
                :panel="p.value"
            >
                <wa-icon
                    v-if="getProviderIcon(p.value)"
                    family="brands"
                    :name="getProviderIcon(p.value)"
                    auto-width
                    class="ad-tab-icon"
                ></wa-icon>
                {{ getProviderLabel(p.value) }}<span v-if="!isEnabled(p.value)" class="ad-disabled-badge">disabled</span>
            </wa-tab>

            <wa-tab-panel v-for="p in providers" :key="p.value" :name="p.value">
                <wa-select
                    v-if="loadSources(p.value).length"
                    value=""
                    @change="loadFrom(p.value, $event)"
                    placeholder="Load from…"
                    size="small"
                    class="ad-load-select"
                >
                    <wa-option v-for="src in loadSources(p.value)" :key="src.key" :value="src.key" :label="src.label">
                        {{ src.label }}
                    </wa-option>
                </wa-select>

                <template v-for="field in supportedFields(p.value)" :key="field">
                    <!-- Model row: registry-driven groups -->
                    <div v-if="field === 'selected_model'" class="ad-field">
                        <label class="ad-field-label">{{ fieldLabel(p.value, 'selected_model') }}</label>
                        <wa-select
                            size="small"
                            :value.prop="toSentinel(fieldValue(p.value, 'selected_model'))"
                            @change="setField(p.value, 'selected_model', $event.target.value)"
                        >
                            <wa-option :value="DEFAULT_SENTINEL">Inherit</wa-option>
                            <small class="ad-group-label">Force to:</small>
                            <template v-for="(group, gi) in modelGroups(p.value)" :key="gi">
                                <wa-divider v-if="gi > 0 && group.entries.length"></wa-divider>
                                <wa-option
                                    v-for="entry in group.entries"
                                    :key="entry.value"
                                    :value="entry.value"
                                >
                                    {{ entry.label }}
                                </wa-option>
                            </template>
                        </wa-select>
                    </div>
                    <!-- Generic row: flat choices -->
                    <div v-else class="ad-field">
                        <label class="ad-field-label">{{ fieldLabel(p.value, field) }}</label>
                        <wa-select
                            size="small"
                            :value.prop="toSentinel(fieldValue(p.value, field))"
                            @change="setField(p.value, field, $event.target.value)"
                        >
                            <wa-option :value="DEFAULT_SENTINEL">Inherit</wa-option>
                            <small class="ad-group-label">Force to:</small>
                            <wa-option
                                v-for="opt in fieldChoices(p.value, field)"
                                :key="String(opt.value)"
                                :value="String(opt.value)"
                                :label="opt.label"
                            >
                                <span>{{ opt.label }}</span>
                                <span v-if="opt.description" class="ad-option-description">{{ opt.description }}</span>
                            </wa-option>
                        </wa-select>
                    </div>
                </template>

                <div v-if="!supportedFields(p.value).length" class="form-hint">
                    This provider has no configurable agent settings.
                </div>
            </wa-tab-panel>
        </wa-tab-group>
    </div>
</template>

<style scoped>
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.ad-intro {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.ad-provider-select {
    max-width: 280px;
}

.provider-option-icon {
    margin-right: 0.5em;
}

.ad-tab-icon {
    margin-right: var(--wa-space-2xs);
}

.ad-load-select {
    max-width: 280px;
    margin-bottom: var(--wa-space-s);
}

.ad-disabled-badge {
    margin-left: var(--wa-space-2xs);
    padding: 0 var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
}

.ad-field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    margin-bottom: var(--wa-space-s);
}

.ad-field-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.ad-group-label {
    display: block;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-style: italic;
}

.ad-option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
</style>
