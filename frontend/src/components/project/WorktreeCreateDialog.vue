<script setup>
// WorktreeCreateDialog.vue - Dialog to create a git worktree of a project.
// A single mounted instance serves every project row: the parent project is
// passed to open(project), not as a prop.
import { ref, computed, nextTick, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import { apiFetch } from '../../utils/api'
import DirectoryPickerPopup from '../files/DirectoryPickerPopup.vue'

const emit = defineEmits(['created'])

const store = useDataStore()

const dialogRef = ref(null)
const branchInputRef = ref(null)
const createButtonRef = ref(null)

// Parent project (set by open()); the worktree is created from its repo.
const parentProject = ref(null)

const localBranch = ref('')
const localPath = ref('')
const localStartFrom = ref('')
const branches = ref([]) // [{ name, checked_out }]
const isCreating = ref(false)
const errorMessage = ref('')

// Unique form ID per instance to avoid conflicts when multiple dialog instances coexist in the DOM
const instanceId = useId()
const formId = `worktree-create-form-${instanceId}`

const parentName = computed(() => {
    const p = parentProject.value
    return p?.name || p?.directory || p?.id || ''
})

const trimmedBranch = computed(() => localBranch.value.trim())

// True when the typed branch name doesn't match any local branch (=> git will
// create it with -b, and the "start from" select becomes meaningful).
const branchIsNew = computed(
    () => !!trimmedBranch.value && !branches.value.some(b => b.name === trimmedBranch.value)
)

// Branch suggestions: the full list when the field is empty (so existing
// branches are discoverable as soon as the dialog opens), then filtered by the
// typed text (case-insensitive). Hidden once the field is an exact match.
const branchSuggestions = computed(() => {
    const typed = trimmedBranch.value.toLowerCase()
    if (branches.value.some(b => b.name === trimmedBranch.value)) return []
    if (!typed) return branches.value
    return branches.value.filter(b => b.name.toLowerCase().includes(typed))
})

const pathPlaceholder = computed(() => {
    const root = parentProject.value?.git_root || '/path/to/repo'
    return `e.g. ${root}/.worktrees/<branch>`
})

/**
 * Set form attribute on the create button when the dialog opens.
 * wa-button doesn't expose `form` as a property, so we must use setAttribute.
 */
function syncFormState() {
    nextTick(() => {
        if (createButtonRef.value) {
            createButtonRef.value.setAttribute('form', formId)
        }
    })
}

// Guard the dialog's show/after-show handlers against events bubbling up from
// child wa-select (its dropdown emits the same wa-show / wa-after-show events).
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    branchInputRef.value?.focus()
}

async function fetchBranches() {
    branches.value = []
    const projectId = parentProject.value?.id
    if (!projectId) return
    let response
    try {
        response = await apiFetch(`/api/projects/${projectId}/branches/`)
    } catch (error) {
        return // autocomplete just stays empty; creation still works
    }
    if (!response.ok) return
    const data = await response.json()
    // Guard against a stale response after the dialog was reopened for
    // another project while the fetch was in flight.
    if (parentProject.value?.id !== projectId) return
    branches.value = Array.isArray(data.branches) ? data.branches : []
}

/**
 * Open the dialog for the given parent project (the repo to create a worktree of).
 */
function open(project) {
    parentProject.value = project
    localBranch.value = ''
    localPath.value = ''
    localStartFrom.value = ''
    errorMessage.value = ''
    isCreating.value = false
    syncFormState()
    fetchBranches()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function onBranchInput(event) {
    localBranch.value = event.target.value
}

function onPathInput(event) {
    localPath.value = event.target.value
}

function applySuggestion(suggestion) {
    if (suggestion.checked_out) return
    localBranch.value = suggestion.name
    branchInputRef.value?.focus()
}

async function handleCreate() {
    if (isCreating.value) return

    const branch = trimmedBranch.value
    const path = localPath.value.trim()
    if (!branch) {
        errorMessage.value = 'Branch is required'
        return
    }
    if (!path || !path.startsWith('/')) {
        errorMessage.value = 'Path must be an absolute path'
        return
    }

    isCreating.value = true
    errorMessage.value = ''

    const body = {
        path,
        branch,
        // Never leak a previously selected start-from into an existing-branch
        // submission (the field is hidden in that case).
        start_from: branchIsNew.value && localStartFrom.value ? localStartFrom.value : null,
    }

    let response
    try {
        response = await apiFetch(`/api/projects/${parentProject.value.id}/worktrees/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
    } catch (error) {
        errorMessage.value = 'Network error. Please try again.'
        isCreating.value = false
        return
    }

    if (!response.ok) {
        const data = await response.json()
        errorMessage.value = data.error || 'Failed to create worktree'
        isCreating.value = false
        return
    }

    const createdProject = await response.json()
    // Required before emitting: the follow-up flow (trust gate, draft session
    // settings resolution) reads the project from the store; the later WS
    // broadcast is an idempotent upsert.
    store.addProject(createdProject)
    emit('created', createdProject)
    isCreating.value = false
    close()
}

defineExpose({
    open,
    close,
})
</script>

<template>
    <wa-dialog ref="dialogRef" label="New worktree" class="worktree-create-dialog" @wa-show="handleDialogShow" @wa-after-show="handleDialogAfterShow">
        <div slot="label" class="dialog-title">
            <span class="dialog-title-text">New worktree</span>
            <span v-if="parentName" class="dialog-title-path">Create a git worktree of {{ parentName }}</span>
        </div>
        <form :id="formId" class="dialog-content" @submit.prevent="handleCreate">
            <div class="form-group">
                <label class="form-label">New or existing branch</label>
                <wa-input
                    ref="branchInputRef"
                    :value.prop="localBranch"
                    @input="onBranchInput"
                    placeholder="feature/my-branch"
                ></wa-input>
                <div v-if="branchSuggestions.length > 0" class="branch-suggestions">
                    <button
                        v-for="suggestion in branchSuggestions"
                        :key="suggestion.name"
                        type="button"
                        class="branch-suggestion"
                        :class="{ 'branch-suggestion-disabled': suggestion.checked_out }"
                        :disabled="suggestion.checked_out"
                        @click="applySuggestion(suggestion)"
                    >
                        <wa-icon name="code-branch" auto-width></wa-icon>
                        <span class="branch-suggestion-name">{{ suggestion.name }}</span>
                        <span v-if="suggestion.checked_out" class="branch-suggestion-hint">in use</span>
                    </button>
                </div>
                <div class="form-hint">
                    An existing branch is checked out into the worktree; an unknown name creates a new branch.
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Path</label>
                <div class="directory-input-row">
                    <wa-input
                        :value.prop="localPath"
                        @input="onPathInput"
                        :placeholder="pathPlaceholder"
                        class="directory-input"
                    ></wa-input>
                    <DirectoryPickerPopup v-model="localPath" :fallback-path="parentProject?.git_root || ''" />
                </div>
                <div class="form-hint">Absolute path where the worktree will be created</div>
            </div>

            <div v-if="branchIsNew && branches.length > 0" class="form-group">
                <label class="form-label">Start from</label>
                <wa-select
                    :value.prop="localStartFrom"
                    @change="localStartFrom = $event.target.value"
                    size="small"
                >
                    <wa-option value="">Current HEAD</wa-option>
                    <wa-option v-for="b in branches" :key="b.name" :value="b.name">{{ b.name }}</wa-option>
                </wa-select>
                <div class="form-hint">The new branch starts from this ref</div>
            </div>

            <wa-callout v-if="errorMessage" variant="danger" size="small" class="error-callout">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isCreating">
                Cancel
            </wa-button>
            <wa-button ref="createButtonRef" type="submit" variant="brand" :loading="isCreating" :disabled="isCreating">
                Create worktree
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.worktree-create-dialog {
    --width: min(550px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.dialog-title {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
}

.dialog-title-path {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
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

.directory-input-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.directory-input {
    flex: 1;
    min-width: 0;
}

/* -- Branch autocomplete ------------------------------------------------------ */
.branch-suggestions {
    display: flex;
    flex-direction: column;
    max-height: 160px;
    overflow-y: auto;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
}

.branch-suggestion {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    background: none;
    border: none;
    cursor: pointer;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-base);
    text-align: left;
}

.branch-suggestion:hover:not(:disabled) {
    background: var(--wa-color-surface-alt);
}

.branch-suggestion-disabled {
    cursor: not-allowed;
    color: var(--wa-color-text-quiet);
}

.branch-suggestion-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.branch-suggestion-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
