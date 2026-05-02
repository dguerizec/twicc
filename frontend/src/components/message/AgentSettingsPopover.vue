<script setup>
// Per-session agent settings popover anchored on the trigger button rendered
// by MessageInput. State, watchers and provider dispatch live in the
// ``useSessionAgentSettings`` composable; this component is the rendering
// surface — both the catalogue (which selects appear, what each contains)
// and the warning copy ("Claude Code process will be stopped…") are driven
// by the session's provider via ``settings.providerHelpers``.
import { computed } from 'vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { formatPresetSummary } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import { CONTEXT_MAX as CLAUDE_CODE_CONTEXT_MAX, EFFORT as CLAUDE_CODE_EFFORT } from '../../providers/claude_code/constants'
import ClaudePresetsDialog from '../app/ClaudePresetsDialog.vue'

const props = defineProps({
    for: { type: String, required: true },
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
    isDraft: { type: Boolean, default: false },
    messageText: { type: String, default: '' },
    buttonLabel: { type: String, default: 'Send' },
})

const {
    isStarting,
    processState,
    selectedPermissionMode,
    selectedModel,
    selectedEffort,
    selectedThinking,
    selectedClaudeInChrome,
    selectedContextMax,
    permissionModeOptions,
    effortOptions,
    thinkingOptions,
    claudeInChromeOptions,
    contextMaxOptions,
    modelRegistryOptions,
    defaultModelLabel,
    defaultContextMaxLabel,
    defaultEffortLabel,
    defaultThinkingLabel,
    defaultClaudeInChromeLabel,
    defaultPermissionModeLabel,
    isContextMaxForced,
    isContextMaxForcedByModel,
    isEffortXhighAvailable,
    isEffortMaxAvailable,
    contextMaxSelectValue,
    anySettingForced,
    hasDropdownsChanged,
    presets,
    hasPresets,
    claudePresetsDialogOpen,
    handlePresetSelect,
    restoreSettings,
    startupChanges,
    providerHelpers,
} = props.settings

const providerLabel = computed(() => providerHelpers.value?.constructor?.label ?? 'Agent')

// Warning shown when applying the pending changes will require a process
// stop/restart (i.e. at least one ``startup``-category setting differs from
// what's currently active). Returns null when no warning is needed.
const startupSettingsWarning = computed(() => {
    if (!startupChanges.value?.startup.length) return null

    const state = processState.value?.state
    const hasCrons = processState.value?.active_crons?.length > 0
    const prefix = state === 'assistant_turn'
        ? `Once ${providerLabel.value} finishes its current work, the`
        : 'The'
    const hasText = props.messageText.trim()
    if (hasCrons) {
        const suffix = hasText ? ', after which your message will be sent.' : '.'
        return `${prefix} ${providerLabel.value} process will be stopped to apply these settings, then resumed to restart the current cron jobs${suffix}`
    }
    const suffix = hasText
        ? 'Your message will be sent after the process restarts.'
        : 'Your next message will resume the session.'
    return `${prefix} ${providerLabel.value} process will be stopped to apply these settings. ${suffix}`
})

function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}
</script>

<template>
    <wa-popover
        v-popover-focus-fix
        :for="props.for"
        placement="top"
        class="settings-popover"
    >
        <!-- Apply preset / Reset / Manage (non-scrollable) -->
        <div class="settings-panel-presets">
            <wa-dropdown @wa-select="handlePresetSelect">
                <wa-button slot="trigger" size="small" appearance="outlined" :disabled="isStarting">
                    <wa-icon slot="start" name="sliders"></wa-icon>
                    Reset / Presets
                    <wa-icon slot="end" name="caret-down"></wa-icon>
                </wa-button>
                <wa-dropdown-item value="__reset__" :disabled="!anySettingForced">
                    <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                    Reset to defaults
                </wa-dropdown-item>
                <wa-divider></wa-divider>
                <wa-dropdown-item v-if="hasPresets" disabled>Presets</wa-dropdown-item>
                <wa-dropdown-item
                    v-for="(preset, i) in presets"
                    :key="i"
                    :value="String(i)"
                    class="preset-item"
                >
                    <span>{{ preset.name }}</span>
                    <span class="option-description">{{ formatPresetSummary(preset, providerHelpers) }}</span>
                </wa-dropdown-item>
                <wa-divider v-if="hasPresets"></wa-divider>
                <wa-dropdown-item value="__manage__">
                    <wa-icon slot="icon" name="pen-to-square"></wa-icon>
                    Manage presets
                </wa-dropdown-item>
            </wa-dropdown>
        </div>

        <!-- Actions & callouts (non-scrollable) — hidden on drafts since there's no process to apply to -->
        <div v-if="(!isDraft && hasDropdownsChanged) || startupSettingsWarning" class="settings-panel-actions">
            <div v-if="!isDraft && hasDropdownsChanged" class="settings-panel-links">
                <a class="settings-action-link" @click.prevent="restoreSettings">
                    <wa-icon name="xmark"></wa-icon>
                    Discard unsaved changes
                </a>
            </div>
            <wa-callout v-if="!isDraft && hasDropdownsChanged" variant="brand" class="settings-info-callout">
                <wa-icon name="circle-info" slot="icon"></wa-icon>
                Click "{{ buttonLabel }}" to apply your changes.
            </wa-callout>
            <wa-callout v-if="startupSettingsWarning" variant="warning" class="startup-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ startupSettingsWarning }}
            </wa-callout>
        </div>

        <!-- Settings dropdowns (scrollable) -->
        <div class="settings-panel">
            <!-- Model -->
            <div v-if="providerHelpers?.supportsAgentSetting('selected_model')" class="setting-row">
                <label class="setting-label">Model</label>
                <wa-select
                    :value.prop="selectedModel === null ? DEFAULT_SENTINEL : selectedModel"
                    @change="selectedModel = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value"
                    size="small"
                    :disabled="isStarting"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultModelLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option
                        v-for="entry in modelRegistryOptions.latest"
                        :key="entry.selected_model"
                        :value="entry.selected_model"
                    >
                        {{ providerHelpers.getModelLabel(entry.selected_model) }} (latest: {{ entry.version }})
                    </wa-option>
                    <wa-divider v-if="modelRegistryOptions.older.length"></wa-divider>
                    <wa-option
                        v-for="entry in modelRegistryOptions.older"
                        :key="entry.selected_model"
                        :value="entry.selected_model"
                    >
                        {{ providerHelpers.getModelLabel(entry.selected_model) }} (until {{ formatRetirementDate(entry.retirement_date) }})
                    </wa-option>
                </wa-select>
                <a v-if="selectedModel !== null" class="reset-setting-link" @click.prevent="selectedModel = null">Reset to default: {{ defaultModelLabel }}</a>
            </div>

            <!-- Context -->
            <div v-if="providerHelpers?.supportsAgentSetting('context_max')" class="setting-row">
                <label class="setting-label">Context</label>
                <wa-select
                    :value.prop="contextMaxSelectValue"
                    @change="selectedContextMax = $event.target.value === DEFAULT_SENTINEL ? null : Number($event.target.value)"
                    size="small"
                    :disabled="isStarting || isContextMaxForced || isContextMaxForcedByModel"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultContextMaxLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option v-for="option in contextMaxOptions" :key="option.value" :value="String(option.value)">
                        {{ option.label }}
                    </wa-option>
                </wa-select>
                <span v-if="isContextMaxForced" class="setting-help">Forced to 1M: context usage exceeds 85% of 200K.</span>
                <span v-else-if="isContextMaxForcedByModel" class="setting-help">1M not available for this model version.</span>
                <a v-else-if="selectedContextMax !== null" class="reset-setting-link" @click.prevent="selectedContextMax = null">Reset to default: {{ defaultContextMaxLabel }}</a>
            </div>

            <!-- Effort -->
            <div v-if="providerHelpers?.supportsAgentSetting('effort')" class="setting-row">
                <label class="setting-label">Effort</label>
                <wa-select
                    :value.prop="selectedEffort === null ? DEFAULT_SENTINEL : selectedEffort"
                    @change="selectedEffort = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value"
                    size="small"
                    :disabled="isStarting"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultEffortLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option
                        v-for="option in effortOptions"
                        :key="option.value"
                        :value="option.value"
                        :disabled="(option.value === CLAUDE_CODE_EFFORT.X_HIGH && !isEffortXhighAvailable) || (option.value === CLAUDE_CODE_EFFORT.MAX && !isEffortMaxAvailable)"
                    >
                        {{ option.label }}{{ ((option.value === CLAUDE_CODE_EFFORT.X_HIGH && !isEffortXhighAvailable) || (option.value === CLAUDE_CODE_EFFORT.MAX && !isEffortMaxAvailable)) ? ' (not available)' : '' }}
                    </wa-option>
                </wa-select>
                <a v-if="selectedEffort !== null" class="reset-setting-link" @click.prevent="selectedEffort = null">Reset to default: {{ defaultEffortLabel }}</a>
            </div>

            <!-- Thinking -->
            <div v-if="providerHelpers?.supportsAgentSetting('thinking_enabled')" class="setting-row">
                <label class="setting-label">Thinking</label>
                <wa-select
                    :value.prop="selectedThinking === null ? DEFAULT_SENTINEL : String(selectedThinking)"
                    @change="selectedThinking = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value === 'true'"
                    size="small"
                    :disabled="isStarting"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultThinkingLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option v-for="option in thinkingOptions" :key="String(option.value)" :value="String(option.value)">
                        {{ option.label }}
                    </wa-option>
                </wa-select>
                <a v-if="selectedThinking !== null" class="reset-setting-link" @click.prevent="selectedThinking = null">Reset to default: {{ defaultThinkingLabel }}</a>
            </div>

            <!-- Permission -->
            <div v-if="providerHelpers?.supportsAgentSetting('permission_mode')" class="setting-row">
                <label class="setting-label">Permission</label>
                <wa-select
                    :value.prop="selectedPermissionMode === null ? DEFAULT_SENTINEL : selectedPermissionMode"
                    @change="selectedPermissionMode = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value"
                    size="small"
                    :disabled="isStarting"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultPermissionModeLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option v-for="option in permissionModeOptions" :key="option.value" :value="option.value" :label="option.label">
                        <span>{{ option.label }}</span>
                        <span class="option-description">{{ option.description }}</span>
                    </wa-option>
                </wa-select>
                <a v-if="selectedPermissionMode !== null" class="reset-setting-link" @click.prevent="selectedPermissionMode = null">Reset to default: {{ defaultPermissionModeLabel }}</a>
            </div>

            <!-- Claude in Chrome -->
            <div v-if="providerHelpers?.supportsAgentSetting('claude_in_chrome')" class="setting-row">
                <label class="setting-label">Claude built-in Chrome MCP</label>
                <wa-select
                    :value.prop="selectedClaudeInChrome === null ? DEFAULT_SENTINEL : String(selectedClaudeInChrome)"
                    @change="selectedClaudeInChrome = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value === 'true'"
                    size="small"
                    :disabled="isStarting"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultClaudeInChromeLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option v-for="option in claudeInChromeOptions" :key="String(option.value)" :value="String(option.value)">
                        {{ option.label }}
                    </wa-option>
                </wa-select>
                <a v-if="selectedClaudeInChrome !== null" class="reset-setting-link" @click.prevent="selectedClaudeInChrome = null">Reset to default: {{ defaultClaudeInChromeLabel }}</a>
            </div>
        </div>

        <ClaudePresetsDialog v-model:open="claudePresetsDialogOpen" />
    </wa-popover>
</template>

<style scoped>
.settings-popover {
    --max-width: min(30rem, 100vw);
    --arrow-size: 12px;
    &::part(body) {
        max-height: calc(100vh - 8rem);
        display: flex;
        flex-direction: column;
    }
}

.settings-panel {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    overflow-y: auto;
    flex: 1;
    min-height: 0;
}

.settings-info-callout,
.startup-warning-callout {
    font-size: var(--wa-font-size-xs);
    width: 100%;
}

.settings-panel-presets {
    display: flex;
    justify-content: center;
    flex-shrink: 0;
    padding-bottom: var(--wa-space-s);
    wa-dropdown::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}

.settings-panel-actions {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    padding-bottom: var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-border);
}

.settings-panel-links {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs) var(--wa-space-s);
    justify-content: center;
}

.settings-action-link {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    &:hover {
        text-decoration: underline;
    }
}

.setting-row {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.setting-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.setting-help {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.select-group-label {
    display: block;
    padding: var(--wa-space-3xs) var(--wa-space-l);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.reset-setting-link {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.preset-item::part(label) {
    white-space: normal;
    max-width: 25rem;
}
</style>
