<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { DURATION_PRESETS, presetToDate } from '../../utils/datePresets'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    open: { type: Boolean, required: true },
    preset: { type: String, required: true },        // e.g. '7d'
    scope: { type: Object, required: true },         // { type, id }
})

const emit = defineEmits(['update:open', 'archived'])

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

const currentPreset = ref(props.preset)
watch(() => props.preset, (v) => { currentPreset.value = v })

const count = ref(null)         // null = loading, number = result
const error = ref(null)
const submitting = ref(false)

let abortController = null

const currentLabel = computed(
    () => DURATION_PRESETS.find((p) => p.value === currentPreset.value)?.label ?? currentPreset.value,
)

const workspace = computed(() => {
    if (props.scope.type !== 'workspace') return null
    return workspacesStore.workspaces.find((w) => w.id === props.scope.id) ?? null
})

const workspaceProjectIds = computed(() => {
    if (!workspace.value) return []
    return workspacesStore.getVisibleProjectIds(workspace.value.id)
})

async function refreshCount() {
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        // Defensive: shouldn't happen since wa-select only emits known presets,
        // but bail gracefully rather than send a malformed request.
        error.value = `Unknown duration preset: ${currentPreset.value}`
        return
    }

    if (abortController) abortController.abort()
    abortController = new AbortController()

    count.value = null
    error.value = null

    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
            dryRun: true,
            signal: abortController.signal,
        })
        count.value = res.count
    } catch (err) {
        if (err.name === 'AbortError') return
        error.value = err.message || 'Failed to fetch count.'
    }
}

watch(() => props.open, (isOpen) => {
    if (!isOpen) return
    if (currentPreset.value === props.preset) {
        // Preset unchanged since last open: the currentPreset watcher won't fire,
        // so trigger the refresh ourselves.
        refreshCount()
    } else {
        // Preset changed since last open: assigning here will trigger the
        // currentPreset watcher, which calls refreshCount once.
        currentPreset.value = props.preset
    }
})

watch(currentPreset, () => {
    if (props.open) refreshCount()
})

onUnmounted(() => {
    if (abortController) abortController.abort()
})

async function handleConfirm() {
    if (count.value === 0 || count.value === null || submitting.value) return
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        error.value = `Unknown duration preset: ${currentPreset.value}`
        return
    }
    submitting.value = true
    error.value = null
    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
        })
        emit('archived', { count: res.count })
        emit('update:open', false)
    } catch (err) {
        error.value = err.message || 'Failed to archive sessions.'
    } finally {
        submitting.value = false
    }
}

function handleCancel() {
    emit('update:open', false)
}

// Wire the submit button to the form by id (Web Awesome wa-button does not
// expose `form` as a property — must be set via setAttribute on the host).
const submitButtonRef = ref(null)
const FORM_ID = 'bulk-archive-confirm-form'
watch(submitButtonRef, async (el) => {
    if (el) {
        await nextTick()
        el.setAttribute('form', FORM_ID)
    }
})

function handleDialogHide(event) {
    // wa-hide bubbles up from nested wa-select panels too — only react to the
    // dialog's own hide, otherwise changing the duration would close the dialog.
    if (event.target !== event.currentTarget) return
    if (submitting.value) {
        event.preventDefault()
        return
    }
    if (props.open) emit('update:open', false)
}
</script>

<template>
    <wa-dialog
        :open="open"
        label="Archive sessions"
        @wa-hide="handleDialogHide"
        style="--width: min(520px, calc(100vw - 2rem));"
    >
        <form :id="FORM_ID" @submit.prevent="handleConfirm">
            <div class="bulk-archive-row">
                <span>Archive sessions older than</span>
                <wa-select v-model="currentPreset" size="small" class="duration-select">
                    <wa-option
                        v-for="p in DURATION_PRESETS"
                        :key="p.value"
                        :value="p.value"
                    >{{ p.label }}</wa-option>
                </wa-select>
            </div>

            <div class="bulk-archive-scope">
                <div class="bulk-archive-scope-main">
                    <span class="bulk-archive-scope-label">Scope:</span>
                    <ProjectBadge v-if="scope.type === 'project'" :project-id="scope.id" />
                    <template v-else-if="scope.type === 'workspace' && workspace">
                        <wa-icon
                            name="layer-group"
                            auto-width
                            :style="workspace.color ? { color: workspace.color } : null"
                        ></wa-icon>
                        <span>{{ workspace.name }}</span>
                    </template>
                    <span v-else>All projects</span>
                </div>
                <div v-if="scope.type === 'workspace' && workspace" class="workspace-projects">
                    <span class="bulk-archive-scope-label">Workspace projects:</span>
                    <ProjectBadge
                        v-for="pid in workspaceProjectIds"
                        :key="pid"
                        :project-id="pid"
                    />
                </div>
            </div>

            <div class="bulk-archive-count">
                <template v-if="count === null && !error">
                    <wa-spinner></wa-spinner>
                    <span>Counting…</span>
                </template>
                <template v-else-if="count === 0">
                    No sessions to archive in this scope older than {{ currentLabel }}.
                </template>
                <template v-else-if="count !== null">
                    This will archive <strong>{{ count }}</strong> session{{ count > 1 ? 's' : '' }}.
                </template>
            </div>

            <div class="bulk-archive-hint">
                Sessions that are pinned or have an active process are excluded.
            </div>

            <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>
        </form>

        <wa-button slot="footer" appearance="plain" :disabled="submitting" @click="handleCancel">
            Cancel
        </wa-button>
        <wa-button
            slot="footer"
            ref="submitButtonRef"
            type="submit"
            variant="brand"
            :disabled="count === null || count === 0 || submitting"
            :loading="submitting"
        >
            <template v-if="count && count > 0">Archive {{ count }}</template>
            <template v-else>Archive</template>
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
.bulk-archive-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-m);
    flex-wrap: wrap;
}

.duration-select {
    min-width: 8rem;
}

.bulk-archive-scope {
    margin-bottom: var(--wa-space-m);
}

.bulk-archive-scope-main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.bulk-archive-scope-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.workspace-projects {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
}

.bulk-archive-count {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-xs);
    min-height: 1.5rem;
}

.bulk-archive-hint {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
