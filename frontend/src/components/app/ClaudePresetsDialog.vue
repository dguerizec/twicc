<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useClaudeCodeStore } from '../../providers/claude_code/store'
import {
    PERMISSION_MODE,
    PERMISSION_MODE_LABELS,
    EFFORT,
    EFFORT_LABELS,
    THINKING_LABELS,
    CLAUDE_IN_CHROME_LABELS,
    CONTEXT_MAX,
    CONTEXT_MAX_LABELS,
    getModelLabel,
} from '../../constants'
import { claudeCodeHelpers } from '../../providers/claude_code/helpers'
import { formatPresetSummary } from '../../utils/presetFormat'

const DEFAULT_SENTINEL = '__default__'

const props = defineProps({
    open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const claudeCodeStore = useClaudeCodeStore()

const view = ref('list')
const editIndex = ref(null)
const formData = ref(emptyFormData())
const errorMessage = ref('')

const dialogRef = ref(null)
const nameInputRef = ref(null)
const submitButtonRef = ref(null)

const presets = computed(() => claudeCodeStore.settingsPresets)

const dialogLabel = computed(() => {
    if (view.value === 'list') return 'Claude config presets'
    return editIndex.value === null ? 'Add preset' : 'Edit preset'
})

const modelOptions = computed(() => {
    const registry = claudeCodeHelpers.getModelRegistry() || []
    return registry.map((entry) => {
        const baseLabel = getModelLabel(entry.selected_model)
        return {
            value: entry.selected_model,
            label: entry.latest ? `${baseLabel} (latest)` : baseLabel,
        }
    })
})

const contextOptions = Object.values(CONTEXT_MAX).map((value) => ({
    value: String(value),
    raw: value,
    label: CONTEXT_MAX_LABELS[value],
}))

const effortOptions = Object.values(EFFORT).map((value) => ({ value, label: EFFORT_LABELS[value] }))

const permissionOptions = Object.values(PERMISSION_MODE).map((value) => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
}))

const thinkingOptions = [
    { value: 'true', raw: true, label: THINKING_LABELS[true] },
    { value: 'false', raw: false, label: THINKING_LABELS[false] },
]

const chromeOptions = [
    { value: 'true', raw: true, label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', raw: false, label: CLAUDE_IN_CHROME_LABELS[false] },
]

function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}

function fromSentinel(stringValue, parser = (v) => v) {
    if (stringValue === DEFAULT_SENTINEL) return null
    return parser(stringValue)
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

function emptyFormData() {
    return {
        name: '',
        model: null,
        context_max: null,
        effort: null,
        thinking: null,
        permission_mode: null,
        claude_in_chrome: null,
    }
}

function handleDelete(index) {
    claudeCodeStore.deleteSettingsPreset(index)
}

function handleDuplicate(index) {
    claudeCodeStore.duplicateSettingsPreset(index)
}

function handleReorder(index, direction) {
    claudeCodeStore.reorderSettingsPreset(index, direction)
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

// List → Form transitions (filled in Task 8/9)
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
    formData.value = { ...source }
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
    const trimmedName = formData.value.name.trim()
    if (!trimmedName) {
        errorMessage.value = 'Name is required'
        return
    }
    if (claudeCodeStore.findSettingsPresetIndexByName(trimmedName, editIndex.value) !== -1) {
        errorMessage.value = 'A preset with this name already exists'
        return
    }
    const payload = { ...formData.value, name: trimmedName }
    if (editIndex.value === null) {
        claudeCodeStore.addSettingsPreset(payload)
    } else {
        claudeCodeStore.updateSettingsPreset(editIndex.value, payload)
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
                        <span class="preset-summary">{{ formatPresetSummary(preset) }}</span>
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

                <div class="form-group">
                    <label class="form-label">Model</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.model)"
                        @change="formData.model = fromSentinel($event.target.value)"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in modelOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

                <div class="form-group">
                    <label class="form-label">Context size</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.context_max)"
                        @change="formData.context_max = fromSentinel($event.target.value, (v) => Number(v))"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in contextOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

                <div class="form-group">
                    <label class="form-label">Effort</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.effort)"
                        @change="formData.effort = fromSentinel($event.target.value)"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in effortOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

                <div class="form-group">
                    <label class="form-label">Thinking</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.thinking)"
                        @change="formData.thinking = fromSentinel($event.target.value, (v) => v === 'true')"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in thinkingOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

                <div class="form-group">
                    <label class="form-label">Permission mode</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.permission_mode)"
                        @change="formData.permission_mode = fromSentinel($event.target.value)"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in permissionOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

                <div class="form-group">
                    <label class="form-label">Chrome MCP</label>
                    <wa-select
                        size="small"
                        :value.prop="toSentinel(formData.claude_in_chrome)"
                        @change="formData.claude_in_chrome = fromSentinel($event.target.value, (v) => v === 'true')"
                    >
                        <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                        <small class="select-group-label">Force to:</small>
                        <wa-option v-for="opt in chromeOptions" :key="opt.value" :value="opt.value">
                            {{ opt.label }}
                        </wa-option>
                    </wa-select>
                </div>

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
</style>
