<script setup>
// ArtifactBookmarkDialog.vue — create/edit (and remove) an artifact bookmark.
// Mirrors ProjectEditDialog.vue's form/submit/focus/guard patterns.
import { ref, computed, nextTick, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getSessionGrantsForBookmark } from '../../artifact-broker/host'
import ShareDialog from '../share/ShareDialog.vue'

const props = defineProps({
    sessionId: { type: String, default: null },
    relativePath: { type: String, default: null },
})
const emit = defineEmits(['saved', 'removed'])
const store = useDataStore()
const settingsStore = useSettingsStore()

// Share entry point (edit mode only — an existing bookmark id is required).
const showShareDialog = ref(false)
const existingAllowedHosts = ref({})
const shareSessionGrants = ref({})
const sharingEnabled = computed(() => !!settingsStore.getShareBaseUrl)
function openShareDialog() {
    // Snapshot the artifact's session-only broker grants at open, like
    // ProjectView's global handler — the create dialog offers to promote them.
    shareSessionGrants.value = getSessionGrantsForBookmark(existingId.value)
    showShareDialog.value = true
}

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)

const existingId = ref(null)
const localName = ref('')
const localScope = ref('project')
const isSaving = ref(false)
const errorMessage = ref('')

const isEditMode = computed(() => existingId.value !== null)
const instanceId = useId()
const formId = `artifact-bookmark-form-${instanceId}`

// wa-button doesn't expose `form` as a property — set it via setAttribute.
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) saveButtonRef.value.setAttribute('form', formId)
    })
}

function focusName() {
    const input = nameInputRef.value
    if (!input) return
    input.focus()
    const len = input.value?.length || 0
    input.setSelectionRange?.(len, len)
}

// Guard against wa-show / wa-after-show bubbling up from the nested wa-select.
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}
function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    focusName()
}

/** Open in create mode (existing = null) or edit mode (an existing bookmark). */
function open(existing = null) {
    errorMessage.value = ''
    isSaving.value = false
    if (existing) {
        existingId.value = existing.id
        localName.value = existing.name || ''
        localScope.value = existing.scope || 'project'
        existingAllowedHosts.value = existing.allowed_hosts || {}
    } else {
        existingId.value = null
        localName.value = ''
        localScope.value = 'project'
    }
    syncFormState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

async function handleSave() {
    if (isSaving.value) return
    const name = localName.value.trim()
    if (!name) {
        errorMessage.value = 'Name is required'
        return
    }
    isSaving.value = true
    errorMessage.value = ''
    try {
        if (isEditMode.value) {
            await store.updateArtifactBookmark(existingId.value, { name, scope: localScope.value })
        } else {
            await store.createArtifactBookmark({
                sessionId: props.sessionId,
                relativePath: props.relativePath,
                name,
                scope: localScope.value,
            })
        }
        emit('saved')
        close()
    } catch (err) {
        errorMessage.value = err?.message || 'Failed to save bookmark'
    } finally {
        isSaving.value = false
    }
}

async function handleRemove() {
    if (isSaving.value || !isEditMode.value) return
    isSaving.value = true
    errorMessage.value = ''
    try {
        await store.deleteArtifactBookmark(existingId.value)
        emit('removed')
        close()
    } catch (err) {
        errorMessage.value = err?.message || 'Failed to remove bookmark'
    } finally {
        isSaving.value = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="isEditMode ? 'Edit artifact bookmark' : 'Bookmark artifact'"
        class="artifact-bookmark-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">Name</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="localName"
                    @input="localName = $event.target.value"
                    placeholder="Bookmark name"
                    maxlength="255"
                ></wa-input>
            </div>
            <div class="form-group">
                <label class="form-label">Scope</label>
                <wa-select
                    :value.prop="localScope"
                    @change="localScope = $event.target.value"
                    size="small"
                >
                    <wa-option value="project">Bookmark in project</wa-option>
                    <wa-option value="workspace">Bookmark in workspace</wa-option>
                    <wa-option value="all">Bookmark everywhere</wa-option>
                </wa-select>
                <div class="form-hint">Where this bookmark shows in the Artifacts sidebar.</div>
            </div>
            <wa-callout v-if="errorMessage" variant="danger" size="small">{{ errorMessage }}</wa-callout>
        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button
                v-if="isEditMode"
                variant="danger"
                appearance="outlined"
                class="footer-remove"
                :disabled="isSaving"
                @click="handleRemove"
            >
                Remove
            </wa-button>
            <wa-button
                v-if="isEditMode"
                variant="neutral"
                appearance="outlined"
                :disabled="!sharingEnabled"
                :title="sharingEnabled ? 'Share this artifact' : 'Configure a share host in Settings → Sharing'"
                @click="openShareDialog"
            >
                <wa-icon name="share-nodes" slot="start"></wa-icon>
                Share…
            </wa-button>
            <wa-button variant="neutral" appearance="outlined" :disabled="isSaving" @click="close">Cancel</wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                {{ isEditMode ? 'Save' : 'Bookmark' }}
            </wa-button>
        </div>
        <ShareDialog v-if="isEditMode" :open="showShareDialog" kind="artifact"
                     :bookmark-id="existingId" :allowed-hosts="existingAllowedHosts"
                     :session-grants="shareSessionGrants"
                     :default-title="localName" @close="showShareDialog = false" />
    </wa-dialog>
</template>

<style scoped>
.artifact-bookmark-dialog {
    --width: min(440px, calc(100vw - 2rem));
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
.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
.footer-remove {
    margin-right: auto;
}
</style>
