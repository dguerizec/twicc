<script setup>
// Provider-agnostic agent-settings popover anchored on the trigger button
// rendered by MessageInput. Every render decision (which fields appear,
// which options are disabled, what the help text says, what the startup
// warning reads, which dialog the "Manage presets" button opens) is
// resolved through hooks on the session's provider helpers — see the
// "Agent settings popover/summary rendering hooks" section in
// ``BaseProviderHelpers``.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { presetSummaryParts, bundleSummaryParts } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import { getProviderOptions, getProviderHelpers, getProviderIcon } from '../../providers'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import AgentSettingsPresetsDialog from '../app/AgentSettingsPresetsDialog.vue'
import AgentSettingsSummaryView from './AgentSettingsSummaryView.vue'

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
    selectedModel,
    selectedPermissionMode,
    selectedEffort,
    selectedThinking,
    selectedClaudeInChrome,
    selectedContextMax,
    SELECTED_REFS,
    summaryState,
    effectiveModel,
    isContextMaxForced,
    anySettingForced,
    hasDropdownsChanged,
    presets,
    hasPresets,
    presetsDialogOpen,
    handlePresetSelect,
    restoreSettings,
    resetAllToDefaults,
    resetStack,
    startupChanges,
    providerHelpers,
    providerStore,
} = props.settings

const dataStore = useDataStore()
const settings = useSettingsStore()

// Non-image attachments currently held by the draft. Computed off the
// store's reactive Map so the labelled wa-callout below reacts to add /
// remove without ad-hoc wiring.
const nonImageAttachments = computed(() => {
    const sid = props.settings.sessionId.value
    if (!sid) return []
    return dataStore.getAttachments(sid).filter(m => m.type !== 'image')
})

// Provider switcher (drafts only) — list of registered providers with
// their icon and active-disabled flag. Options that don't accept the
// draft's current attachment mix stay clickable: the click handler
// intercepts the switch, surfaces a callout, and lets the user resolve
// the conflict before retrying.
const providerSwitcherOptions = computed(() => {
    // Only show providers that are usable right now (intent-enabled AND
    // in running state). The current provider stays in the list even if
    // it is in a transient state — switching away from it is a valid
    // user intent that the runtime gate will handle on the new turn.
    const current = props.session?.provider
    return getProviderOptions()
        .filter(opt => opt.value === current || dataStore.isProviderAvailable(opt.value))
        .map(opt => ({
            value: opt.value,
            label: opt.label,
            icon: getProviderIcon(opt.value),
            active: opt.value === current,
        }))
})

const currentProviderIcon = computed(() => getProviderIcon(props.session?.provider))

const currentProviderLabel = computed(() => providerHelpers.value?.constructor.label ?? null)

// Provider id of the most recent rejected switch attempt — used to
// surface a transient warning callout asking the user to drop the
// blocking attachments before retrying. Cleared automatically when the
// blocking attachments are gone (so removing them via any other path,
// e.g. the attachments popover, also dismisses the callout).
const blockedSwitchTargetProvider = ref(null)
const blockedSwitchTargetLabel = computed(() => {
    const target = blockedSwitchTargetProvider.value
    if (!target) return null
    if (nonImageAttachments.value.length === 0) return null
    const opt = providerSwitcherOptions.value.find(o => o.value === target)
    return opt?.label ?? null
})

function handleProviderSelect(event) {
    const provider = event.detail?.item?.value
    if (!provider) return
    if (provider === props.session?.provider) return

    // Intercept the switch when the target provider can't accept the
    // current non-image attachments: surface the callout and leave the
    // session on its current provider. The user can either drop the
    // attachments via the callout's inline action (which retries the
    // switch automatically) or back out by selecting something else.
    const optHelpers = getProviderHelpers(provider)
    const support = optHelpers?.getAttachmentSupport() ?? null
    const docsBlocked = nonImageAttachments.value.length > 0 && support?.documents === false
    if (docsBlocked) {
        blockedSwitchTargetProvider.value = provider
        return
    }

    blockedSwitchTargetProvider.value = null
    dataStore.setDraftProvider(props.settings.sessionId.value, provider)
    // Reset every per-session override so the bundle follows the new
    // provider's defaults.
    resetAllToDefaults()
}

async function removeBlockingDocuments() {
    const sid = props.settings.sessionId.value
    if (!sid) return
    const target = blockedSwitchTargetProvider.value
    await dataStore.removeNonImageAttachments(sid)
    // Auto-retry the switch the user originally asked for so the click
    // sequence "select Codex → Remove them" doesn't require a third
    // click on the dropdown.
    blockedSwitchTargetProvider.value = null
    if (target && target !== props.session?.provider) {
        dataStore.setDraftProvider(sid, target)
        resetAllToDefaults()
    }
}

// Order of the rows below the model row. ``supportsAgentSetting`` filters
// each entry per-provider so a field nobody declares is silently skipped.
const SIMPLE_FIELDS = ['context_max', 'effort', 'thinking_enabled', 'permission_mode', 'claude_in_chrome', 'fast_mode']

const defaults = computed(() => summaryState.value.defaults)

// The session's current EFFECTIVE value per wire field (the user's selection,
// else the resolved default). Fed to the preset / reset summaries so each can
// dashed-underline the fields that DIFFER from the current choice — i.e. what
// applying that preset / reset would actually change.
const currentEffective = computed(() => {
    const sel = summaryState.value.selected
    const def = summaryState.value.defaults
    const pick = (f) => sel[f] ?? def[f]
    return {
        selected_model: pick('selected_model'),
        context_max: pick('context_max'),
        effort: pick('effort'),
        thinking_enabled: pick('thinking_enabled'),
        permission_mode: pick('permission_mode'),
        claude_in_chrome: pick('claude_in_chrome'),
        fast_mode: pick('fast_mode'),
    }
})

// Render-time context fed to the rendering hooks. Hooks ignore keys they
// don't need; this lets us assemble it once per render and pass it through.
const baseContext = computed(() => ({
    sessionId: props.settings.sessionId.value,
    isStarting: isStarting.value,
    isContextMaxForced: isContextMaxForced.value,
    effectiveModel: effectiveModel.value,
}))

function fieldContext(field) {
    return {
        ...baseContext.value,
        field,
        selectedValue: SELECTED_REFS[field]?.value ?? null,
        defaultValue: defaults.value[field],
    }
}

const startupSettingsWarning = computed(() => {
    if (!startupChanges.value?.startup.length) return null
    const helpers = providerHelpers.value
    if (!helpers) return null
    return helpers.getStartupWarningText({
        processStateName: processState.value?.state ?? null,
        hasMessageText: Boolean(props.messageText.trim()),
        hasCrons: (processState.value?.active_crons?.length ?? 0) > 0,
    })
})

// Only surfaces during ASSISTANT_TURN for idle-only diffs: in USER_TURN
// the next Send applies idle changes immediately via the SDK, and any
// concurrent startup change subsumes idle ones under the startup
// kill/restart — so the startup callout wins when both exist.
const idleSettingsWarning = computed(() => {
    if (!startupChanges.value?.idle.length) return null
    if (startupChanges.value?.startup.length) return null
    if (processState.value?.state !== 'assistant_turn') return null
    const helpers = providerHelpers.value
    if (!helpers) return null
    return helpers.getIdleWarningText({
        hasMessageText: Boolean(props.messageText.trim()),
    })
})

// Setting rows below the model row — one v-for over all simple fields the
// provider declares. The model row is rendered separately because it
// consumes ``getModelSelectGroups`` instead of a flat choices list.
const simpleFieldRows = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers) return []
    return SIMPLE_FIELDS
        .filter(field => helpers.supportsAgentSetting(field))
        .map(field => {
            const ctx = fieldContext(field)
            const selectedValue = SELECTED_REFS[field]?.value ?? null
            const defaultValue = defaults.value[field]
            return {
                field,
                label: helpers.getFieldLabel(field),
                value: helpers.getDisplayedSelectValue(field, selectedValue, ctx),
                defaultLabel: helpers.getDefaultValueLabel(field, defaultValue),
                fieldDisabled: helpers.isFieldDisabled(field, ctx),
                helpText: helpers.getFieldHelpText(field, ctx),
                selectedValue,
                choices: helpers.getFieldChoices(field).map(opt => {
                    const disabled = helpers.isChoiceDisabled(field, opt.value, ctx)
                    return {
                        value: String(opt.value),
                        rawValue: opt.value,
                        label: opt.label,
                        description: opt.description ?? null,
                        labelWithSuffix: disabled ? `${opt.label} (not available)` : opt.label,
                        disabled,
                    }
                }),
            }
        })
})

const modelRow = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers || !helpers.supportsAgentSetting('selected_model')) return null
    const ctx = fieldContext('selected_model')
    return {
        label: helpers.getFieldLabel('selected_model'),
        value: helpers.getDisplayedSelectValue('selected_model', selectedModel.value, ctx),
        defaultLabel: helpers.getDefaultValueLabel('selected_model', defaults.value.selected_model),
        fieldDisabled: helpers.isFieldDisabled('selected_model', ctx),
        helpText: helpers.getFieldHelpText('selected_model', ctx),
        groups: helpers.getModelSelectGroups(helpers.getModelRegistry()),
    }
})

function onSelectChange(field, event) {
    const ref_ = SELECTED_REFS[field]
    if (!ref_) return
    const raw = event.target.value
    if (raw === DEFAULT_SENTINEL) {
        // Snapshot model: "Default: X" pins the field to the current resolved
        // default (concrete), not a NULL "follow".
        ref_.value = defaults.value[field]
        return
    }
    // Look the option up by stringified value so the typed (boolean / number /
    // string) original is restored from the wa-select's string binding.
    const choices = providerHelpers.value?.getFieldChoices(field) ?? []
    const match = choices.find(opt => String(opt.value) === raw)
    ref_.value = match ? match.value : raw
}

function onModelChange(event) {
    const raw = event.target.value
    selectedModel.value = raw === DEFAULT_SENTINEL ? defaults.value.selected_model : raw
}

function resetField(field) {
    const ref_ = SELECTED_REFS[field]
    if (ref_) ref_.value = defaults.value[field]
}

const popoverRef = ref(null)

function focusFirstElement() {
    const pop = popoverRef.value
    if (!pop) return
    // wa-button[slot="trigger"] targets the inner wa-dropdown triggers
    // (provider switcher, Reset/Presets); wa-select covers the model row
    // and simple-field rows. First DOM match wins, which mirrors the
    // top-to-bottom order of the popover.
    const first = pop.querySelector('wa-button[slot="trigger"]:not([disabled]), wa-select:not([disabled])')
    first?.focus()
}

function handleAfterShow(e) {
    // Ignore wa-after-show events that bubble up from inner wa-select /
    // wa-dropdown menus — only the popover's own open event should trigger
    // first-element focus.
    if (e.target !== popoverRef.value) return
    focusFirstElement()
}

function closeAndFocusTextarea() {
    // Pre-focus the textarea so vPopoverFocusFix lands focus there after
    // close — without this the focus would fall on a now-hidden element
    // inside the popover.
    document.querySelector('.message-input wa-textarea')?.focus()
    popoverRef.value?.hide()
}

function handleToggleShortcut() {
    const pop = popoverRef.value
    if (!pop) return
    if (pop.open) {
        closeAndFocusTextarea()
    } else {
        pop.show()
    }
}

function handlePopoverKeydown(e) {
    // Escape inside the popover: close it and return focus to the textarea.
    // Replaces the native dialog Escape close (which would put focus on the
    // trigger button instead) and prevents the Escape from reaching App.vue's
    // triple-Escape counter.
    if (e.key === 'Escape' && popoverRef.value?.open) {
        e.preventDefault()
        e.stopPropagation()
        closeAndFocusTextarea()
    }
}

onMounted(() => {
    window.addEventListener('twicc:toggle-agent-settings', handleToggleShortcut)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:toggle-agent-settings', handleToggleShortcut)
})
</script>

<template>
    <wa-popover
        ref="popoverRef"
        v-popover-focus-fix
        @wa-after-show="handleAfterShow"
        @keydown="handlePopoverKeydown"
        :for="props.for"
        placement="top"
        class="settings-popover"
    >
        <!-- Provider switch blocked by non-image attachments — appears
             only after the user actually attempts a switch to a
             provider that doesn't accept the current attachments, so an
             otherwise-uninterested user never sees the callout. -->
        <wa-callout
            v-if="isDraft && blockedSwitchTargetLabel"
            variant="warning"
            class="provider-blocked-callout"
        >
            <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
            {{ blockedSwitchTargetLabel }}
            cannot accept the {{ nonImageAttachments.length }} non-image
            attachment{{ nonImageAttachments.length > 1 ? 's' : '' }} on this draft.
            <a class="settings-action-link" @click.prevent="removeBlockingDocuments">
                Remove {{ nonImageAttachments.length > 1 ? 'them' : 'it' }} and switch
            </a>
        </wa-callout>

        <!-- Apply preset / Reset / Manage (non-scrollable) -->
        <div class="settings-panel-presets">
            <!-- Provider switcher: drafts only. Switching resets every per-
                 session override so the bundle follows the new provider's
                 defaults. -->
            <wa-dropdown v-if="isDraft && providerSwitcherOptions.length > 1" @wa-select="handleProviderSelect">
                <wa-button slot="trigger" size="small" appearance="outlined">
                    <wa-icon
                        v-if="currentProviderIcon"
                        slot="start"
                        auto-width
                        family="brands"
                        :name="currentProviderIcon"
                    ></wa-icon>
                    {{ currentProviderLabel ?? 'Provider' }}
                    <wa-icon slot="end" name="caret-down"></wa-icon>
                </wa-button>
                <wa-dropdown-item
                    v-for="opt in providerSwitcherOptions"
                    :key="opt.value"
                    :value="opt.value"
                    :disabled="opt.active"
                >
                    <wa-icon
                        v-if="opt.icon"
                        slot="icon"
                        auto-width
                        family="brands"
                        :name="opt.icon"
                    ></wa-icon>
                    {{ opt.label }}
                </wa-dropdown-item>
            </wa-dropdown>
            <wa-dropdown @wa-select="handlePresetSelect">
                <wa-button slot="trigger" size="small" appearance="outlined" :disabled="isStarting">
                    <wa-icon slot="start" name="sliders"></wa-icon>
                    Reset / Presets
                    <wa-icon slot="end" name="caret-down"></wa-icon>
                </wa-button>
                <!-- Reset stack: re-apply the project's resolved defaults, an
                     ancestor project's, or the global defaults — each shown with
                     the concrete values it would set (like a preset). Collapses
                     to a single "Reset to defaults" when no project in the chain
                     sets any. See useSessionAgentSettings.resetStack. -->
                <template v-if="resetStack.length === 1">
                    <wa-dropdown-item :value="resetStack[0].key" :disabled="!anySettingForced" class="reset-item">
                        <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                        <span>{{ resetStack[0].label }}</span>
                        <AgentSettingsSummaryView class="option-description" :parts="bundleSummaryParts(resetStack[0].bundle, providerHelpers, currentEffective)" />
                    </wa-dropdown-item>
                </template>
                <template v-else>
                    <wa-dropdown-item disabled>Reset to…</wa-dropdown-item>
                    <wa-dropdown-item
                        v-for="target in resetStack"
                        :key="target.key"
                        :value="target.key"
                        class="reset-item"
                    >
                        <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                        <span>{{ target.label }}</span>
                        <AgentSettingsSummaryView class="option-description" :parts="bundleSummaryParts(target.bundle, providerHelpers, currentEffective)" />
                    </wa-dropdown-item>
                </template>
                <wa-divider></wa-divider>
                <wa-dropdown-item v-if="hasPresets" disabled>Presets</wa-dropdown-item>
                <wa-dropdown-item
                    v-for="(preset, i) in presets"
                    :key="i"
                    :value="String(i)"
                    class="preset-item"
                >
                    <span>{{ preset.name }}</span>
                    <AgentSettingsSummaryView class="option-description" :parts="presetSummaryParts(preset, providerHelpers, currentEffective)" />
                </wa-dropdown-item>
                <wa-divider v-if="hasPresets"></wa-divider>
                <wa-dropdown-item value="__manage__">
                    <wa-icon slot="icon" name="pen-to-square"></wa-icon>
                    Manage presets
                </wa-dropdown-item>
            </wa-dropdown>
        </div>

        <!-- Actions & callouts (non-scrollable) — hidden on drafts since there's no process to apply to -->
        <div v-if="(!isDraft && hasDropdownsChanged) || startupSettingsWarning || idleSettingsWarning" class="settings-panel-actions">
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
            <wa-callout v-else-if="idleSettingsWarning" variant="warning" class="idle-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ idleSettingsWarning }}
            </wa-callout>
        </div>

        <!-- Settings dropdowns (scrollable) -->
        <div class="settings-panel">
            <!-- Model row (special: registry-driven groups instead of a flat choices list) -->
            <div v-if="modelRow" class="setting-row">
                <label class="setting-label">{{ modelRow.label }}</label>
                <wa-select
                    :value.prop="modelRow.value"
                    @change="onModelChange"
                    size="small"
                    :disabled="modelRow.fieldDisabled"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ modelRow.defaultLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <template v-for="(group, gi) in modelRow.groups" :key="gi">
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
                <span v-if="modelRow.helpText" class="setting-help">{{ modelRow.helpText }}</span>
                <a v-else-if="modelRow.value !== DEFAULT_SENTINEL" class="reset-setting-link" @click.prevent="resetField('selected_model')">Reset to default: {{ modelRow.defaultLabel }}</a>
            </div>

            <!-- Other rows -->
            <div
                v-for="row in simpleFieldRows"
                :key="row.field"
                class="setting-row"
            >
                <label class="setting-label">{{ row.label }}</label>
                <wa-select
                    :value.prop="row.value"
                    @change="onSelectChange(row.field, $event)"
                    size="small"
                    :disabled="row.fieldDisabled"
                >
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ row.defaultLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option
                        v-for="opt in row.choices"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.labelWithSuffix"
                        :disabled="opt.disabled"
                    >
                        <span>{{ opt.labelWithSuffix }}</span>
                        <span v-if="opt.description" class="option-description">{{ opt.description }}</span>
                    </wa-option>
                </wa-select>
                <span v-if="row.helpText" class="setting-help">{{ row.helpText }}</span>
                <a v-else-if="row.value !== DEFAULT_SENTINEL" class="reset-setting-link" @click.prevent="resetField(row.field)">Reset to default: {{ row.defaultLabel }}</a>
            </div>
        </div>

        <AgentSettingsPresetsDialog
            v-if="session?.provider"
            v-model:open="presetsDialogOpen"
            :provider="session.provider"
        />
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
.startup-warning-callout,
.idle-warning-callout,
.provider-blocked-callout {
    font-size: var(--wa-font-size-s);
    width: 100%;
}

.provider-blocked-callout {
    flex-shrink: 0;
    margin-bottom: var(--wa-space-l);

    /* The "Remove …" affordance rides inline with the explanatory
       sentence rather than dropping onto its own line — overrides the
       default block-flex layout the shared link class carries elsewhere. */
    .settings-action-link {
        display: inline;
        color: inherit;
        text-decoration: underline;
    }
}

.settings-panel-presets {
    display: flex;
    justify-content: center;
    gap: var(--wa-space-xs);
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
    font-size: var(--wa-font-size-s);
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

.preset-item::part(label),
.reset-item::part(label) {
    white-space: normal;
    max-width: 25rem;
}
</style>
