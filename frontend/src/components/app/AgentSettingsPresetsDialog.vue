<script setup>
// Provider-agnostic dialog to manage the agent settings presets of a given
// provider. Opens the list view by default; "Add" / "Edit" switch to a
// form that renders one row per agent setting field the provider supports.
//
// Each row uses the same rendering hooks as ``AgentSettingsPopover``
// (``getFieldLabel``, ``getFieldChoices``, ``getModelSelectGroups``) so a
// new provider that ships its own catalog needs zero template changes here.
//
// Preset persistence and CRUD live on each provider's store —
// ``settingsPresets`` / ``addSettingsPreset`` / ``updateSettingsPreset`` /
// ``deleteSettingsPreset`` / ``duplicateSettingsPreset`` /
// ``reorderSettingsPreset`` / ``findSettingsPresetIndexByName``. The dialog
// expects this contract; a provider that doesn't expose it must not surface
// the "Manage presets" entry that opens this dialog.

import { computed, nextTick, ref, watch } from 'vue'
import { getProviderHelpers, getProviderStore } from '../../providers'
import { formatPresetSummary } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'

const props = defineProps({
    open: { type: Boolean, default: false },
    provider: { type: String, required: true },
})
const emit = defineEmits(['update:open'])

const providerHelpers = computed(() => getProviderHelpers(props.provider))
const providerStore = computed(() => getProviderStore(props.provider))
const providerLabel = computed(() => providerHelpers.value?.constructor?.label ?? 'Agent')

// Preset records use historical key names (``model``, ``thinking``) while
// the helpers' rendering hooks are keyed on wire names
// (``selected_model``, ``thinking_enabled``). The form keeps wire names
// internally and we translate at the persistence boundary.
const WIRE_TO_PRESET_KEY = {
    selected_model: 'model',
    context_max: 'context_max',
    effort: 'effort',
    thinking_enabled: 'thinking',
    permission_mode: 'permission_mode',
    claude_in_chrome: 'claude_in_chrome',
}
const FIELD_ORDER = ['selected_model', 'context_max', 'effort', 'thinking_enabled', 'permission_mode', 'claude_in_chrome']

const view = ref('list')
const editIndex = ref(null)
const formData = ref(emptyFormData())
const errorMessage = ref('')

const dialogRef = ref(null)
const nameInputRef = ref(null)
const submitButtonRef = ref(null)

const presets = computed(() => providerStore.value?.settingsPresets ?? [])

const dialogLabel = computed(() => {
    if (view.value === 'list') return `${providerLabel.value} settings presets`
    return editIndex.value === null ? 'Add preset' : 'Edit preset'
})

// Fields the provider declares — drives the rows of the edit form. The
// model row uses ``getModelSelectGroups``; the rest use a flat
// ``getFieldChoices`` list.
const supportedFields = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers) return []
    return FIELD_ORDER.filter(f => helpers.supportsAgentSetting(f))
})

const modelGroups = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers) return []
    return helpers.getModelSelectGroups(helpers.getModelRegistry())
})

function emptyFormData() {
    return {
        name: '',
        selected_model: null,
        context_max: null,
        effort: null,
        thinking_enabled: null,
        permission_mode: null,
        claude_in_chrome: null,
    }
}

function presetToFormData(preset) {
    const data = emptyFormData()
    data.name = preset.name ?? ''
    for (const wire of FIELD_ORDER) {
        const presetKey = WIRE_TO_PRESET_KEY[wire]
        data[wire] = preset[presetKey] ?? null
    }
    return data
}

function formDataToPreset(data) {
    const preset = { name: data.name }
    for (const wire of FIELD_ORDER) {
        const presetKey = WIRE_TO_PRESET_KEY[wire]
        preset[presetKey] = data[wire]
    }
    return preset
}

function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}

// Reads the wa-select's current string back into the typed value used by
// the form. Falls back to the raw string if no choice matches (defensive —
// shouldn't happen with the static catalogues we ship today).
function fromSentinel(field, raw) {
    if (raw === DEFAULT_SENTINEL) return null
    if (field === 'selected_model') return raw
    const choices = providerHelpers.value?.getFieldChoices(field) ?? []
    const match = choices.find(opt => String(opt.value) === raw)
    return match ? match.value : raw
}

watch(
    () => view.value,
    async (newView) => {
        if (newView !== 'form') return
        await nextTick()
        const btn = submitButtonRef.value
        if (btn) btn.setAttribute?.('form', 'preset-form')
    },
)

function handleDelete(index) {
    providerStore.value?.deleteSettingsPreset(index)
}

function handleDuplicate(index) {
    providerStore.value?.duplicateSettingsPreset(index)
}

function handleReorder(index, direction) {
    providerStore.value?.reorderSettingsPreset(index, direction)
}

function closeDialog() {
    emit('update:open', false)
}

function onAfterShow() {
    view.value = 'list'
    editIndex.value = null
    formData.value = emptyFormData()
    errorMessage.value = ''
}

function openAddForm() {
    formData.value = emptyFormData()
    editIndex.value = null
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function openEditForm(index) {
    const source = presets.value[index]
    if (!source) return
    formData.value = presetToFormData(source)
    editIndex.value = index
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
}

async function focusNameInput() {
    await nextTick()
    const el = nameInputRef.value
    if (!el) return
    el.focus?.()
    if (typeof el.setSelectionRange === 'function') {
        const len = el.value?.length || 0
        el.setSelectionRange(len, len)
    }
}

function handleSave() {
    errorMessage.value = ''
    const store = providerStore.value
    if (!store) {
        errorMessage.value = 'Provider unavailable'
        return
    }
    const trimmedName = formData.value.name.trim()
    if (!trimmedName) {
        errorMessage.value = 'Name is required'
        return
    }
    if (store.findSettingsPresetIndexByName(trimmedName, editIndex.value) !== -1) {
        errorMessage.value = 'A preset with this name already exists'
        return
    }
    const payload = formDataToPreset({ ...formData.value, name: trimmedName })
    if (editIndex.value === null) {
        store.addSettingsPreset(payload)
    } else {
        store.updateSettingsPreset(editIndex.value, payload)
    }
    view.value = 'list'
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="manage-presets-dialog"
        :label="dialogLabel"
        :open="props.open"
        @wa-after-show.self="onAfterShow"
        @wa-after-hide.self="closeDialog"
    >
        <div v-if="view === 'list'" class="dialog-content">
            <div v-if="presets.length === 0" class="empty-message">
                No presets yet. Add one to get started.
            </div>
            <div v-else class="preset-list">
                <div v-for="(preset, index) in presets" :key="index" class="preset-row">
                    <div class="reorder-arrows">
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === 0 }"
                            :disabled="index === 0"
                            title="Move up"
                            @click="handleReorder(index, -1)"
                        ><wa-icon name="chevron-up" /></button>
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === presets.length - 1 }"
                            :disabled="index === presets.length - 1"
                            title="Move down"
                            @click="handleReorder(index, 1)"
                        ><wa-icon name="chevron-down" /></button>
                    </div>
                    <div class="preset-display">
                        <span class="preset-name">{{ preset.name }}</span>
                        <span class="preset-summary">{{ formatPresetSummary(preset, providerHelpers) }}</span>
                    </div>
                    <div class="preset-actions">
                        <button class="action-btn" title="Edit" @click="openEditForm(index)">
                            <wa-icon name="pen-to-square" />
                        </button>
                        <button class="action-btn" title="Duplicate" @click="handleDuplicate(index)">
                            <wa-icon name="copy" />
                        </button>
                        <button class="action-btn action-btn-danger" title="Delete" @click="handleDelete(index)">
                            <wa-icon name="trash-can" />
                        </button>
                    </div>
                </div>
            </div>
        </div>
        <form v-else id="preset-form" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label" for="preset-name-input">
                    Name <span class="form-label-quiet">(mandatory)</span>
                </label>
                <wa-input
                    id="preset-name-input"
                    ref="nameInputRef"
                    :value="formData.name"
                    size="small"
                    @input="formData.name = $event.target.value"
                ></wa-input>
            </div>

            <template v-for="field in supportedFields" :key="field">
                <!-- Model row uses registry-driven groups instead of a flat choices list -->
                <div v-if="field === 'selected_model'" class="form-group">
                    <label class="form-label">{{ providerHelpers.getFieldLabel('selected_model') }}</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.selected_model)"
                        @change="formData.selected_model = fromSentinel('selected_model', $event.target.value)"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <template v-for="(group, gi) in modelGroups" :key="gi">
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
                <!-- Generic row: flat list of choices from the provider -->
                <div v-else class="form-group">
                    <label class="form-label">{{ providerHelpers.getFieldLabel(field) }}</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData[field])"
                        @change="formData[field] = fromSentinel(field, $event.target.value)"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option
                            v-for="opt in providerHelpers.getFieldChoices(field)"
                            :key="String(opt.value)"
                            :value="String(opt.value)"
                            :label="opt.label"
                        >
                            <span>{{ opt.label }}</span>
                            <span v-if="opt.description" class="option-description">{{ opt.description }}</span>
                        </wa-option>
                    </wa-select>
                </div>
            </template>

            <wa-callout v-if="errorMessage" variant="danger">{{ errorMessage }}</wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button variant="neutral" appearance="outlined" @click="closeDialog">Close</wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    <wa-icon slot="start" name="plus"></wa-icon>
                    Add preset
                </wa-button>
            </template>
            <template v-else>
                <wa-button variant="neutral" appearance="outlined" @click="cancelForm">Cancel</wa-button>
                <wa-button ref="submitButtonRef" variant="brand" type="submit">Save</wa-button>
            </template>
        </div>
    </wa-dialog>
</template>

<style scoped>
.manage-presets-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    button {
        box-shadow: none;
        margin: 0;
    }
}

.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
}

.preset-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.preset-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

.reorder-arrows {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
}

.reorder-btn {
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-2xs);
    cursor: pointer;
    transition: color 0.15s, background-color 0.15s;
}

.reorder-btn:hover:not(.disabled) {
    color: var(--wa-color-text-base);
    background: var(--wa-color-surface-alt);
}

.reorder-btn.disabled {
    opacity: 0.25;
    cursor: default;
}

.preset-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}

.preset-name {
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.preset-summary {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.preset-actions {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.action-btn {
    background: none;
    border: none;
    font-size: var(--wa-font-size-m);
    padding: var(--wa-space-xs);
    cursor: pointer;
    line-height: 1;
    transition: background-color 0.15s, color 0.15s;
    color: var(--wa-color-text-quiet);
}

.action-btn:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-text);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-label-quiet {
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-normal);
}

.select-group-label {
    display: block;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-style: italic;
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
</style>
