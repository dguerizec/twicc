<script setup>
// Provider-agnostic agent-settings popover anchored on the trigger button
// rendered by MessageInput. Every render decision (which fields appear,
// which options are disabled, what the help text says, what the startup
// warning reads, which dialog the "Manage presets" button opens) is
// resolved through hooks on the session's provider helpers — see the
// "Agent settings popover/summary rendering hooks" section in
// ``BaseProviderHelpers``.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { presetSummaryParts, bundleSummaryParts } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import { getProviderHelpers, getProviderIcon } from '../../providers'
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
    // True while a pending request locks sending: the Send/Apply button is hidden,
    // so the "click X to apply" hint is replaced with one explaining the changes
    // will apply once the request is answered.
    sendingLocked: { type: Boolean, default: false },
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
    sessionIsUntrusted,
    isPermissionModeForced,
    clampedPermissionMode,
    anySettingForced,
    hasDropdownsChanged,
    presetGroups,
    presetsDialogOpen,
    handlePresetSelect,
    providerSwitcherOptions,
    restoreSettings,
    resetAllToDefaults,
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

// Provider selector (drafts only). ``providerSwitcherOptions`` (from the
// composable) lists the providers usable right now — the current one always
// stays, plus every other intent-enabled + running provider. The popover
// renders 2 options as a bi-label switch, >2 as a dropdown, ≤1 as nothing.
const currentProviderIcon = computed(() => getProviderIcon(props.session?.provider))

const currentProviderLabel = computed(() => providerHelpers.value?.constructor.label ?? null)

// Two-provider toggle: the global default provider anchors the "on" (checked)
// side of the switch, the other provider the "off" side. When the configured
// default isn't one of the two offered options (edge case), the first option
// anchors "on" so the toggle keeps a stable meaning.
const defaultProvider = computed(() => {
    const configured = settings.getDefaultProvider
    const opts = providerSwitcherOptions.value
    return opts.some(o => o.value === configured) ? configured : (opts[0]?.value ?? null)
})
const switchOnOption = computed(() => providerSwitcherOptions.value.find(o => o.value === defaultProvider.value) ?? null)
const switchOffOption = computed(() => providerSwitcherOptions.value.find(o => o.value !== defaultProvider.value) ?? null)
const isOnDefault = computed(() => props.session?.provider === defaultProvider.value)

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

// Switch the draft to another provider. Intercepts the switch when the target
// can't accept the current non-image attachments: surfaces the callout and
// leaves the session on its current provider (the user drops the attachments via
// the callout's inline action, which retries, or backs out). Resets every per-
// session override so the bundle follows the new provider's defaults.
function switchToProvider(provider) {
    if (!provider || provider === props.session?.provider) return
    const optHelpers = getProviderHelpers(provider)
    const support = optHelpers?.getAttachmentSupport() ?? null
    const docsBlocked = nonImageAttachments.value.length > 0 && support?.documents === false
    if (docsBlocked) {
        blockedSwitchTargetProvider.value = provider
        return
    }
    blockedSwitchTargetProvider.value = null
    dataStore.setDraftProvider(props.settings.sessionId.value, provider)
    resetAllToDefaults()
}

// >2 providers: dropdown selection.
function handleProviderSelect(event) {
    switchToProvider(event.detail?.item?.value)
}

// 2 providers: the bi-label switch flips to whichever option is not current.
async function onProviderSwitchToggle(event) {
    const target = providerSwitcherOptions.value.find(o => o.value !== props.session?.provider)
    if (target) switchToProvider(target.value)
    // Re-assert the DOM switch from the source of truth: a blocked or no-op
    // switch leaves session.provider (and isOnDefault) unchanged, but wa-switch
    // already flipped its own ``checked`` and Vue won't re-patch an unchanged
    // bound value — realign it explicitly (web-component two-way quirk).
    await nextTick()
    if (event?.target) event.target.checked = isOnDefault.value
}

async function removeBlockingDocuments() {
    const sid = props.settings.sessionId.value
    if (!sid) return
    const target = blockedSwitchTargetProvider.value
    await dataStore.removeNonImageAttachments(sid)
    // Auto-retry the switch the user originally asked for so the click
    // sequence "select Codex → Remove them" doesn't require a third click.
    blockedSwitchTargetProvider.value = null
    if (target) switchToProvider(target)
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
    // Hybrid CLI sessions get their own live/idle/startup classification
    // (e.g. permission_mode becomes startup) — surfaced via field help text.
    isHybrid: props.session?.hybrid === true,
    // Trust clamp (trust design §13.3/§13.4): ``untrusted`` disables
    // permission modes outside the untrusted-allowed set (base
    // ``isChoiceDisabled``); the forced pair makes the select show the value
    // the backend actually applies (base ``getDisplayedSelectValue`` +
    // ``getFieldHelpText``), mirroring the isContextMaxForced machinery.
    untrusted: sessionIsUntrusted.value,
    permissionModeForced: isPermissionModeForced.value,
    clampedPermissionMode: clampedPermissionMode.value,
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
                    const disabledReason = disabled
                        ? helpers.getChoiceDisabledReason(field, opt.value, ctx)
                        : null
                    return {
                        value: String(opt.value),
                        rawValue: opt.value,
                        label: opt.label,
                        description: disabledReason ?? opt.description ?? null,
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
        fallbackNotice: helpers.getModelFallbackNotice(props.session?.selected_model ?? defaults.value.selected_model),
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
    // wa-switch covers the two-provider toggle; wa-button[slot="trigger"]
    // targets the inner wa-dropdown triggers (>2 provider switcher,
    // Reset/Presets); wa-select covers the model row and simple-field rows.
    // First DOM match wins, which mirrors the top-to-bottom order of the popover.
    const first = pop.querySelector('wa-switch:not([disabled]), wa-button[slot="trigger"]:not([disabled]), wa-select:not([disabled])')
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
            <!-- Provider selector: drafts only. 2 providers → bi-label switch
                 (the default provider anchors the "on" side); >2 → dropdown.
                 Switching resets every per-session override so the bundle
                 follows the new provider's defaults. -->
            <div
                v-if="isDraft && providerSwitcherOptions.length === 2"
                class="provider-toggle"
            >
                <span
                    class="provider-toggle-side"
                    :class="{ active: !isOnDefault }"
                    @click="switchToProvider(switchOffOption?.value)"
                >
                    <wa-icon
                        v-if="switchOffOption?.icon"
                        auto-width
                        family="brands"
                        :name="switchOffOption.icon"
                    ></wa-icon>
                    {{ switchOffOption?.label }}
                </span>
                <wa-switch
                    class="provider-toggle-switch"
                    size="small"
                    :checked="isOnDefault"
                    :aria-label="`Switch provider (currently ${currentProviderLabel})`"
                    @change="onProviderSwitchToggle"
                ></wa-switch>
                <span
                    class="provider-toggle-side"
                    :class="{ active: isOnDefault }"
                    @click="switchToProvider(switchOnOption?.value)"
                >
                    <wa-icon
                        v-if="switchOnOption?.icon"
                        auto-width
                        family="brands"
                        :name="switchOnOption.icon"
                    ></wa-icon>
                    {{ switchOnOption?.label }}
                </span>
            </div>
            <wa-dropdown v-else-if="isDraft && providerSwitcherOptions.length > 2" @wa-select="handleProviderSelect">
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
            <!-- Reset / Presets: one dropdown grouped by provider. Each group
                 leads with a "{provider} default" reset entry, then that
                 provider's presets. A draft shows every switchable provider
                 (picking one switches the draft first); a real session lists
                 only its own. See useSessionAgentSettings.presetGroups. -->
            <wa-dropdown @wa-select="handlePresetSelect">
                <wa-button slot="trigger" size="small" appearance="outlined" :disabled="isStarting">
                    <wa-icon slot="start" name="sliders"></wa-icon>
                    Reset / Presets
                    <wa-icon slot="end" name="caret-down"></wa-icon>
                </wa-button>
                <template v-for="(group, gi) in presetGroups" :key="group.provider">
                    <wa-divider v-if="gi > 0"></wa-divider>
                    <!-- Group header: only when more than one provider is shown -->
                    <wa-dropdown-item v-if="presetGroups.length > 1" disabled class="group-header">
                        <wa-icon
                            v-if="group.icon"
                            slot="icon"
                            auto-width
                            family="brands"
                            :name="group.icon"
                        ></wa-icon>
                        {{ group.label }}
                    </wa-dropdown-item>
                    <!-- Reset targets: "{provider} default" always, plus the
                         ancestor / global levels when a project sets its own
                         defaults. See useSessionAgentSettings.resetTargetsForProvider. -->
                    <wa-dropdown-item
                        v-for="(target, ti) in group.resetTargets"
                        :key="target.key"
                        :value="`reset:${group.provider}:${ti}`"
                        :disabled="group.isCurrent && group.resetTargets.length === 1 && !anySettingForced"
                        class="reset-item"
                    >
                        <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                        <span>{{ target.label }}</span>
                        <AgentSettingsSummaryView class="option-description" :parts="bundleSummaryParts(target.bundle, getProviderHelpers(group.provider), group.isCurrent ? currentEffective : null)" />
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-for="p in group.presets"
                        :key="p.index"
                        :value="`preset:${group.provider}:${p.index}`"
                        class="preset-item"
                    >
                        <span>{{ p.preset.name }}</span>
                        <AgentSettingsSummaryView class="option-description" :parts="presetSummaryParts(p.preset, getProviderHelpers(group.provider), group.isCurrent ? currentEffective : null)" />
                    </wa-dropdown-item>
                </template>
                <wa-divider></wa-divider>
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
                <template v-if="sendingLocked">Your changes are saved and will apply once you answer the pending request.</template>
                <template v-else>Click "{{ buttonLabel }}" to apply your changes.</template>
            </wa-callout>
            <wa-callout v-if="startupSettingsWarning" variant="warning" class="startup-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ startupSettingsWarning }}
            </wa-callout>
            <wa-callout v-else-if="idleSettingsWarning" variant="warning" class="idle-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ idleSettingsWarning }}
            </wa-callout>
            <!-- Hybrid CLI advisory: TwiCC never reads back TUI-side changes,
                 so settings must be driven from here. -->
            <wa-callout v-if="session?.hybrid" variant="neutral" class="hybrid-settings-note">
                <wa-icon name="terminal" slot="icon"></wa-icon>
                Hybrid CLI session: change settings here rather than inside the
                terminal — TwiCC does not read back TUI-side changes. Changes
                apply on the next message; some restart the CLI.
            </wa-callout>
        </div>

        <!-- Settings dropdowns (scrollable) -->
        <div class="settings-panel">
            <!-- Model row (special: registry-driven groups instead of a flat choices list) -->
            <div v-if="modelRow" class="setting-row">
                <label class="setting-label">{{ modelRow.label }}</label>
                <wa-callout v-if="modelRow.fallbackNotice" variant="warning" class="model-fallback-callout">
                    {{ modelRow.fallbackNotice }}
                </wa-callout>
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
                            :label="entry.labelWithSuffix"
                            :disabled="entry.disabled"
                        >
                            <span>{{ entry.labelWithSuffix }}</span>
                            <span v-if="entry.description" class="option-description">{{ entry.description }}</span>
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
.model-fallback-callout,
.provider-blocked-callout,
.hybrid-settings-note {
    font-size: var(--wa-font-size-s);
    width: 100%;
}

.model-fallback-callout {
    margin-bottom: 0.4rem;
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
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs) var(--wa-space-m);
    flex-shrink: 0;
    padding-bottom: var(--wa-space-s);
    wa-dropdown::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}

/* Two-provider selector: a label on each side of the switch, the active
   provider highlighted. Mirrors the bi-label toggle pattern used elsewhere
   (e.g. TerminalPanel's scroll/select toggle). */
.provider-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.provider-toggle-side {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    transition: color 0.1s;
    &.active {
        color: var(--wa-color-text-normal);
        font-weight: var(--wa-font-weight-semibold);
    }
    &:hover {
        color: var(--wa-color-text-normal);
    }
}

/* Provider group headers are disabled dropdown-items (non-selectable only);
   undo wa-dropdown-item's disabled dimming (opacity: 0.5) and pin the provider
   name to the normal text color so it reads clearly. */
.group-header {
    opacity: 1 !important;
}
.group-header::part(label) {
    color: var(--wa-color-text-normal);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: var(--wa-font-size-xs);
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
