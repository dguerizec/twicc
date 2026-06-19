<script setup>
// Save the current session's layout to the named-layouts catalog: either overwrite an existing
// named layout, or create a new one (name prefilled "Default"). The "set as global default"
// convenience and the project/global default pickers come with the defaults machinery (step 3).
import { ref, computed, nextTick, useId } from 'vue'
import { useLayoutsStore } from '../../../stores/layouts'

const props = defineProps({
    // The template intention to store (assignment / collapsed / resizeFractions) — the session's
    // current layout at save time, supplied by the parent.
    intention: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['saved'])

const layoutsStore = useLayoutsStore()

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)

// Sentinel for the "create a new one" choice in the target select.
const NEW = '__new__'
const target = ref(NEW)   // either NEW or an existing layout id (overwrite)
const newName = ref('Default')
const errorMessage = ref('')

const instanceId = useId()
const formId = `layout-save-form-${instanceId}`

const existingLayouts = computed(() => layoutsStore.getAllLayouts)
const isNew = computed(() => target.value === NEW)

function syncFormState() {
    nextTick(() => saveButtonRef.value?.setAttribute('form', formId))
}

function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    if (isNew.value && nameInputRef.value) {
        const input = nameInputRef.value
        input.focus()
        const len = input.value?.length || 0
        input.setSelectionRange?.(len, len)
    }
}

function open() {
    errorMessage.value = ''
    target.value = NEW
    newName.value = 'Default'
    syncFormState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

function handleSave() {
    errorMessage.value = ''
    let savedId
    if (isNew.value) {
        const trimmed = newName.value.trim()
        if (!trimmed) {
            errorMessage.value = 'Name cannot be empty.'
            return
        }
        const dup = existingLayouts.value.some((l) => l.name.trim().toLowerCase() === trimmed.toLowerCase())
        if (dup) {
            errorMessage.value = `A layout named "${trimmed}" already exists.`
            return
        }
        savedId = layoutsStore.upsertLayout({ name: trimmed, intention: props.intention })
    } else {
        const existing = layoutsStore.getLayoutById(target.value)
        if (!existing) {
            errorMessage.value = 'That layout no longer exists.'
            return
        }
        savedId = layoutsStore.upsertLayout({ id: existing.id, name: existing.name, intention: props.intention })
    }
    emit('saved', savedId)
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        label="Save layout"
        class="layout-save-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">Save to</label>
                <wa-select
                    :value.prop="target"
                    @change="target = $event.target.value"
                    size="small"
                >
                    <wa-option :value="NEW">New layout…</wa-option>
                    <wa-option v-for="l in existingLayouts" :key="l.id" :value="l.id" :label="`Overwrite “${l.name}”`">
                        Overwrite “{{ l.name }}”
                    </wa-option>
                </wa-select>
            </div>

            <div v-if="isNew" class="form-group">
                <label class="form-label">Name</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="newName"
                    @input="newName = $event.target.value"
                    placeholder="Layout name"
                ></wa-input>
            </div>

            <wa-callout v-if="errorMessage" variant="danger" size="small">{{ errorMessage }}</wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close">Cancel</wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand">Save</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.layout-save-dialog {
    --width: min(420px, calc(100vw - 2rem));
}
.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
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
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
