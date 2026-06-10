<script setup>
// WorktreeCreateDialog.vue - Dialog to create a git worktree of a project.
// A single mounted instance serves every project row: the parent project is
// passed to open(project), not as a prop.
import { ref, computed, watch, nextTick, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { apiFetch } from '../../utils/api'
import { composeWorktreeDir } from '../../utils/worktreePath'
import DirectoryPickerPopup from '../files/DirectoryPickerPopup.vue'
import ProjectBadge from './ProjectBadge.vue'
import WorktreeBadge from './WorktreeBadge.vue'

const emit = defineEmits(['created'])

const store = useDataStore()
const settingsStore = useSettingsStore()

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

// --- Path auto-fill -----------------------------------------------------------
// When a base worktree directory is available (the project's own setting, the
// global default, or a folder picked via the directory picker), the path's last
// segment tracks the branch name automatically — no manual "Append" click.
// `pathBase` is that parent directory; `pathAutoFill` is true while we own the
// path. Typing in the path field by hand hands control back to the user (auto
// off, the "Append" button takes over as a fallback); the picker re-arms it.
const pathBase = ref('')
const pathAutoFill = ref(false)

// Unique form ID per instance to avoid conflicts when multiple dialog instances coexist in the DOM
const instanceId = useId()
const formId = `worktree-create-form-${instanceId}`

// True when the parent repo is itself a git worktree: show the worktree badge
// (main repo + branch folder) instead of the plain project badge, matching how
// the project renders everywhere else in the UI.
const isWorktreeParent = computed(() => !!parentProject.value?.worktree_of)

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

// Branch name turned into a single safe folder name (the project convention:
// slashes and whitespace become dashes, e.g. "feature/add-x" → "feature-add-x").
const branchDirName = computed(() =>
    trimmedBranch.value
        .replace(/[/\s]+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^[-.]+|[-.]+$/g, '')
)

// "Append branch name" is offered when both fields are usable and the path
// doesn't already end with the resolved folder name. With auto-fill on, the
// segment is always present, so this stays hidden — it only resurfaces as a
// manual fallback once the user has taken control of the path field.
const canAppendBranch = computed(() => {
    const dir = branchDirName.value
    const base = localPath.value.trim().replace(/\/+$/, '')
    return !!dir && !!base && !base.endsWith(`/${dir}`)
})

// Join a base directory and a (possibly empty) branch folder segment, dropping
// any trailing slashes on the base. An empty segment yields the bare base.
function joinPath(base, seg) {
    const b = (base || '').replace(/\/+$/, '')
    return seg ? `${b}/${seg}` : b
}

function appendBranchToPath() {
    if (!canAppendBranch.value) return
    localPath.value = joinPath(localPath.value.trim(), branchDirName.value)
}

// Auto-fill: while in auto mode, keep the path as `<base>/<branch-folder>` as the
// user types the branch, replacing the last segment (not appending). Falls back
// to the bare base when the branch is emptied.
watch(branchDirName, (seg) => {
    if (!pathAutoFill.value || !pathBase.value) return
    localPath.value = joinPath(pathBase.value, seg)
})

// Shown under the path field when no base directory could be resolved (no
// per-project worktree directory and no global default) and the user hasn't
// typed or picked a path yet — points them to where a default can be set.
const showNoDefaultHint = computed(() => !pathBase.value && !localPath.value.trim())

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

// Initial value for the path field: the project's own absolute worktree
// directory if set, else the global default composed against the project's git
// root (resolving any "../"), else empty. When non-empty it becomes the auto-fill
// base, so the branch folder is appended live as the user types the branch.
function resolveInitialWorktreePath(project) {
    if (!project) return ''
    const projDir = (project.worktree_directory || '').trim()
    if (projDir) return projDir
    return composeWorktreeDir(project.git_root || '', settingsStore.getDefaultWorktreeDirectory || '')
}

/**
 * Open the dialog for the given parent project (the repo to create a worktree of).
 */
function open(project) {
    parentProject.value = project
    localBranch.value = ''
    const initialPath = resolveInitialWorktreePath(project)
    localPath.value = initialPath
    // Arm auto-fill only when we actually resolved a base directory; the branch
    // folder will then be appended/replaced live as the user types.
    pathBase.value = initialPath
    pathAutoFill.value = !!initialPath
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
    // Hand control back to the user: stop auto-deriving the last segment so we
    // never overwrite what they are typing. The "Append" button is the fallback.
    pathAutoFill.value = false
}

// The directory picker selected a folder: treat it as a fresh base and re-arm
// auto-fill. If the picked folder already ends with the current branch segment
// (the user clicked the worktree folder itself), unwrap it so we don't end up
// with `<base>/<branch>/<branch>`.
function onPickerPath(picked) {
    const trimmed = (picked || '').replace(/\/+$/, '')
    const seg = branchDirName.value
    const base = seg && trimmed.endsWith(`/${seg}`)
        ? trimmed.slice(0, -(seg.length + 1))
        : trimmed
    pathBase.value = base
    pathAutoFill.value = true
    localPath.value = joinPath(base, seg)
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
        <!-- Header: title with the parent project's badge pinned to the right,
             then the action description as a larger subtitle underneath. The
             badge gives the project context at a glance (same treatment as
             ProjectEditDialog's header). -->
        <div slot="label" class="dialog-title">
            <div class="dialog-title-main">
                <span class="dialog-title-text">New worktree</span>
                <WorktreeBadge
                    v-if="isWorktreeParent"
                    :project-id="parentProject.id"
                    class="dialog-title-badge"
                />
                <ProjectBadge
                    v-else-if="parentProject"
                    :project-id="parentProject.id"
                    class="dialog-title-badge"
                />
            </div>
            <span class="dialog-title-subtitle">Create a git worktree</span>
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
                    <DirectoryPickerPopup
                        :model-value="localPath"
                        @update:model-value="onPickerPath"
                        :fallback-path="parentProject?.git_root || ''"
                    />
                </div>
                <div class="form-hint">
                    Absolute path where the worktree will be created
                </div>
                <div v-if="showNoDefaultHint" class="form-hint path-no-default-hint">
                    <wa-icon name="circle-info" auto-width></wa-icon>
                    <span>
                        No default worktree directory set — set one per project
                        (Edit project → Worktree directory) or globally
                        (Settings → Default worktree directory) to pre-fill this path
                        and append the branch name automatically.
                    </span>
                </div>
                <wa-button
                    v-if="canAppendBranch"
                    type="button"
                    class="append-branch-btn"
                    variant="neutral"
                    size="small"
                    @click="appendBranchToPath"
                >
                    <wa-icon slot="start" name="plus"></wa-icon>
                    Append branch name ("{{ branchDirName }}")
                </wa-button>
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

/* Title row: "New worktree" on the left, the parent project's badge pinned to
   the right edge (margin-left:auto). The gap is the minimum separation kept
   when a long badge leaves no free space. */
.dialog-title-main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
    min-width: 0;
}

/* Project badge pinned to the right of the title — a touch smaller; min/max
   keep long names ellipsized rather than overflowing the header. */
.dialog-title-badge {
    margin-left: auto;
    font-size: 0.85em;
    min-width: 0;
    max-width: 100%;
}

/* Action description under the title. Larger than a small path line, since it
   no longer carries the project name (that moved to the badge). */
.dialog-title-subtitle {
    font-size: var(--wa-font-size-m);
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-text-quiet);
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

/* "No default worktree directory" notice: icon + text on one row, the icon
   pinned to the first line. */
.path-no-default-hint {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-2xs);
}

.path-no-default-hint wa-icon {
    margin-top: 0.15em;
    flex-shrink: 0;
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

.append-branch-btn {
    margin-top: var(--wa-space-2xs);
    align-self: flex-start;
    max-width: 100%;
}

.append-branch-btn::part(label) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
