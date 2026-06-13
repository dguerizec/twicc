<script setup>
/**
 * ProviderSettingsSection — generic shell rendered inside the Settings
 * popover for the per-provider section (one section per registered
 * provider). Mirrors the agent-settings popover pattern: every wa-select
 * is driven by hooks on the provider's helpers (``getFieldChoices``,
 * ``getModelSelectGroups``, ``isChoiceDisabled``, ``getDefaultValue``,
 * ``setDefaultValue``), so each provider only needs to override what
 * differs from the neutral defaults defined on ``BaseProviderHelpers``.
 *
 * Unlike the session popover, the values surfaced here are the persisted
 * ``defaults`` (never null): this section only edits global defaults, no
 * "follow default" sentinel is needed.
 */
import { computed, ref } from 'vue'
import { getProviderHelpers, getProviderIcon } from '../../providers'
import { useSettingsStore } from '../../stores/settings'
import AgentSettingsPresetsDialog from './AgentSettingsPresetsDialog.vue'

const props = defineProps({
    provider: {
        type: String,
        required: true,
    },
})

const helpers = computed(() => getProviderHelpers(props.provider))
const providerIcon = computed(() => getProviderIcon(props.provider))

// Field order matches the session popover / presets / project defaults for
// consistency. Fields a provider doesn't support (per
// ``supportsAgentSetting``) are skipped.
const FIELD_ORDER = [
    'selected_model',
    'context_max',
    'effort',
    'thinking_enabled',
    'permission_mode',
    'permission_mode_if_untrusted',
    'claude_in_chrome',
    'fast_mode',
]

// Section-specific labels — distinct from the generic ``getFieldLabel`` so
// the existing UI strings stay stable ("Default permission mode" rather
// than "Default Permission", etc.).
const FIELD_DEFAULT_LABELS = {
    permission_mode: 'Default permission mode',
    permission_mode_if_untrusted: 'Default permission mode (untrusted projects)',
    selected_model: 'Default model',
    context_max: 'Default context size',
    effort: 'Default effort',
    thinking_enabled: 'Default thinking',
    claude_in_chrome: 'Default Chrome MCP',
    fast_mode: 'Default fast mode',
}

const supportedFields = computed(() => FIELD_ORDER.filter(field => helpers.value.supportsAgentSetting(field)))

// Reactive context fed to ``isChoiceDisabled`` / ``isFieldDisabled``. Hors
// d'une session le seul gating utile vient du modèle par défaut lui-même
// (Claude désactive ``effort=X_HIGH`` / ``MAX`` et ``context_max=EXTENDED``
// quand le modèle ne les supporte pas).
const fieldContext = computed(() => ({
    isStarting: false,
    effectiveModel: helpers.value.resolveToAvailableModel(helpers.value.getDefaultValue('selected_model')),
}))

function valueOf(field) {
    return helpers.value.getDefaultValue(field)
}

function onSelectChange(field, event) {
    const raw = event.target.value
    helpers.value.setDefaultValue(field, parseValue(field, raw))
}

// wa-select returns the raw string value of the chosen option. Choice
// catalogues mix booleans (thinking_enabled, claude_in_chrome, fast_mode)
// and integers (context_max) with strings — coerce here to keep the store
// validators happy.
function parseValue(field, raw) {
    if (field === 'thinking_enabled' || field === 'claude_in_chrome' || field === 'fast_mode') {
        return raw === 'true'
    }
    if (field === 'context_max') {
        const n = Number(raw)
        return Number.isFinite(n) ? n : raw
    }
    return raw
}

const modelFallbackNotice = computed(() =>
    helpers.value?.getModelFallbackNotice(valueOf('selected_model')) ?? null,
)

function selectValueOf(field) {
    let v = valueOf(field)
    // A stored default model may have become unavailable (disabled/retired);
    // show its effective substitute so the select isn't left blank (a wa-select
    // can't display a disabled option as selected). Mirrors the session popover.
    if (field === 'selected_model') v = helpers.value.resolveToAvailableModel(v)
    return v === null || v === undefined ? '' : String(v)
}

function isChoiceDisabledFor(field, choiceValue) {
    return helpers.value.isChoiceDisabled(field, choiceValue, fieldContext.value)
}

function modelGroups() {
    const registry = helpers.value.getModelRegistry?.() ?? []
    return helpers.value.getModelSelectGroups(registry)
}

const presetsDialogOpen = ref(false)
function openPresetsDialog() {
    presetsDialogOpen.value = true
}

// --- Orchestration opt-out (soft preference, synced) -----------------------
// Whether agents may pick this provider on their own when orchestrating
// sessions. Soft: an explicit user request for the provider still works.
const settingsStore = useSettingsStore()

const orchestrationEnabled = computed(
    () => !(settingsStore.orchestrationDisabledProviders || []).includes(props.provider),
)

// The last orchestration-enabled provider cannot be switched off — the
// orchestration pool must never end up empty from the UI.
const isLastOrchestrationProvider = computed(
    () => orchestrationEnabled.value && settingsStore.orchestrationProviders.length <= 1,
)

function onOrchestrationToggle(event) {
    const enabled = event.target.checked
    const current = new Set(settingsStore.orchestrationDisabledProviders || [])
    if (enabled) {
        current.delete(props.provider)
    } else {
        if (isLastOrchestrationProvider.value) {
            // Race guard (e.g. stale UI before a WS resync): revert the switch.
            event.target.checked = true
            return
        }
        current.add(props.provider)
    }
    settingsStore.orchestrationDisabledProviders = [...current].sort()
}
</script>

<template>
    <section class="settings-section">
        <h3 class="settings-section-title">
            <wa-icon
                v-if="providerIcon"
                auto-width
                family="brands"
                :name="providerIcon"
            ></wa-icon>
            {{ helpers.constructor.label }} settings
            <wa-icon name="cloud" class="synced-icon"></wa-icon>
        </h3>
        <div
            v-for="field in supportedFields"
            :key="field"
            class="setting-group"
        >
            <label class="setting-group-label">{{ FIELD_DEFAULT_LABELS[field] ?? field }}</label>

            <!-- Model field: groups (latest / older) separated by a divider. -->
            <wa-callout
                v-if="field === 'selected_model' && modelFallbackNotice"
                variant="warning"
                class="model-fallback-callout"
            >
                {{ modelFallbackNotice }}
            </wa-callout>
            <wa-select
                v-if="field === 'selected_model'"
                :value.prop="selectValueOf(field)"
                size="small"
                @change="onSelectChange(field, $event)"
            >
                <template v-for="(group, idx) in modelGroups()" :key="idx">
                    <wa-divider v-if="idx > 0 && group.entries?.length"></wa-divider>
                    <wa-option
                        v-for="entry in group.entries"
                        :key="entry.value"
                        :value="entry.value"
                        :label="entry.labelWithSuffix"
                        :disabled="entry.disabled"
                    >
                        <span>{{ entry.labelWithSuffix }}</span>
                        <span v-if="entry.description" class="option-description">{{ entry.description }}</span>
                    </wa-option>
                </template>
            </wa-select>

            <!-- Other fields: flat choice list, disabled state via helpers. -->
            <wa-select
                v-else
                :value.prop="selectValueOf(field)"
                size="small"
                @change="onSelectChange(field, $event)"
            >
                <wa-option
                    v-for="option in helpers.getFieldChoices(field)"
                    :key="String(option.value)"
                    :value="String(option.value)"
                    :label="option.label"
                    :disabled="isChoiceDisabledFor(field, option.value)"
                >
                    <span>{{ option.label }}{{ isChoiceDisabledFor(field, option.value) ? ' (not available)' : '' }}</span>
                    <span v-if="option.description" class="option-description">{{ option.description }}</span>
                </wa-option>
            </wa-select>
            <span v-if="helpers.getFieldHelpText(field, fieldContext)" class="setting-group-hint">
                {{ helpers.getFieldHelpText(field, fieldContext) }}
            </span>
        </div>

        <wa-divider></wa-divider>

        <div class="setting-group">
            <label class="setting-group-label">Presets</label>
            <span class="setting-group-hint">
                Define bundles of {{ helpers.constructor.label }} settings to quickly apply to a session
            </span>
            <wa-button size="small" @click="openPresetsDialog">
                <wa-icon slot="start" name="sliders"></wa-icon>
                Manage presets…
            </wa-button>
        </div>

        <wa-divider></wa-divider>

        <div class="setting-group">
            <label class="setting-group-label">Orchestration</label>
            <wa-switch
                :checked="orchestrationEnabled"
                :disabled="isLastOrchestrationProvider"
                @change="onOrchestrationToggle"
            >Enabled in orchestration</wa-switch>
            <span class="setting-group-hint">
                When off, agents won't pick {{ helpers.constructor.label }} on their own when
                orchestrating sessions. You can still explicitly ask an agent to use it.
            </span>
            <span v-if="isLastOrchestrationProvider" class="setting-group-hint">
                At least one provider must remain enabled in orchestration.
            </span>
        </div>

        <AgentSettingsPresetsDialog
            v-model:open="presetsDialogOpen"
            :provider="provider"
        />
    </section>
</template>

<style scoped>
.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.synced-icon {
    color: var(--wa-color-brand);
}

.model-fallback-callout {
    display: block;
    font-size: var(--wa-font-size-s);
    margin-bottom: var(--wa-space-xs);
}
</style>
