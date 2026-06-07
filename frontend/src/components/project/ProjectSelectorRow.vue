<script setup>
// ProjectSelectorRow.vue — one project entry in the sidebar project selector
// dropdown. Single source of truth for a project line: the selection check, the
// project badge (color dot + name) and the end-of-line state indicators
// (code comments + aggregated process/unread). Used for both normal projects
// and worktree entries — a worktree is simply the same row at a deeper `depth`,
// with a `label` (its name or just its final folder name) and a `fallbackColor`
// (its main repo's color). Keeping a single row component guarantees worktrees
// stay in lockstep with normal projects for every state indicator.
import { computed, inject } from 'vue'
import { useDataStore } from '../../stores/data'
import ProjectBadge from './ProjectBadge.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'

const props = defineProps({
    projectId: { type: String, required: true },
    // Indent level (0 = flush). paddingLeft = depth * 12px, matching the tree.
    depth: { type: Number, default: 0 },
    // Currently selected project id (drives the leading check mark).
    currentProjectId: { type: String, default: null },
    isAllProjectsMode: { type: Boolean, default: false },
    // Optional display-name override (e.g. a worktree's relative path).
    label: { type: String, default: null },
    // Optional dot color fallback when the project has no color of its own
    // (e.g. a worktree inheriting its main repository's color).
    fallbackColor: { type: String, default: null },
})

const store = useDataStore()

// Provided by ProjectView so any row (named, tree or worktree) can open the
// shared project edit dialog / start a new session without forwarding events
// through every level.
const openProjectEditDialog = inject('openProjectEditDialog', null)
const createSessionInProject = inject('createSessionInProject', null)

const project = computed(() => store.getProject(props.projectId))
const isArchived = computed(() => !!project.value?.archived)
// Stale projects/worktrees have a gone directory — no session can be created
// there, so the "New session" entry is hidden (mirrors the bottom picker).
const isStale = computed(() => !!project.value?.stale)

// Row "…" menu actions. In the template the child dropdown's wa-select is
// `.stop`ped, so it never bubbles to the selector's own selection handler (which
// would navigate); the selector stays open thanks to ProjectView's wa-hide guard.
function onRowMenuSelect(event) {
    const value = event.detail?.item?.value
    if (value === 'new-session') {
        createSessionInProject?.(props.projectId)
    } else if (value === 'edit') {
        openProjectEditDialog?.(props.projectId)
    } else if (value === 'archive') {
        store.setProjectArchived(props.projectId, true)
    } else if (value === 'unarchive') {
        store.setProjectArchived(props.projectId, false)
    }
}
</script>

<template>
    <wa-dropdown-item :value="projectId">
        <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && currentProjectId === projectId ? 'visible' : 'hidden' }"></wa-icon>
        <span class="selector-item-content" :style="depth ? { paddingLeft: `${depth * 12}px` } : null">
            <ProjectBadge :project-id="projectId" :label="label" :fallback-color="fallbackColor" />
            <span class="selector-item-indicators">
                <CodeCommentsIndicator :project-ids="[projectId]" />
                <AggregatedProcessIndicator :project-ids="[projectId]" size="small" />
            </span>
            <!-- Per-row actions menu. The wrapper's @click.stop prevents the
                 trigger/menu clicks from bubbling to the selector's own item
                 click handler (which would navigate to the project). The child
                 dropdown's own trigger toggle still fires (it is wired one level
                 deeper, inside the dropdown's shadow slot). -->
            <span class="row-menu" @click.stop>
                <wa-dropdown placement="bottom-end" @wa-select.stop="onRowMenuSelect">
                    <wa-button slot="trigger" appearance="plain" size="small" class="row-menu-trigger">
                        <wa-icon name="ellipsis" label="Project actions"></wa-icon>
                    </wa-button>
                    <wa-dropdown-item v-if="!isStale" value="new-session">
                        <wa-icon slot="icon" name="plus"></wa-icon>
                        New session
                    </wa-dropdown-item>
                    <wa-divider v-if="!isStale"></wa-divider>
                    <wa-dropdown-item value="edit">
                        <wa-icon slot="icon" name="pencil"></wa-icon>
                        Edit
                    </wa-dropdown-item>
                    <wa-dropdown-item v-if="!isArchived" value="archive">
                        <wa-icon slot="icon" name="box-archive"></wa-icon>
                        Archive
                    </wa-dropdown-item>
                    <wa-dropdown-item v-else value="unarchive">
                        <wa-icon slot="icon" name="box-open"></wa-icon>
                        Unarchive
                    </wa-dropdown-item>
                </wa-dropdown>
            </span>
        </span>
    </wa-dropdown-item>
</template>

<style scoped>
.selector-item-content {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    gap: var(--wa-space-xs);
}

.selector-item-indicators {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    margin-left: auto;
    flex-shrink: 0;
}

/* Per-row "…" actions menu. Always visible (discreet), brighter on hover, and
   occupying a fixed square so every row's indicators stay aligned with the
   worktree-header rows (which reserve the same width but carry no menu). */
.row-menu {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: var(--selector-row-action-size, 1.5rem);
}

.row-menu-trigger {
    opacity: 0.55;
    font-size: var(--wa-font-size-s);
    transition: opacity 0.12s ease;
}

.row-menu-trigger::part(base) {
    padding: 0;
    min-height: 0;
    width: var(--selector-row-action-size, 1.5rem);
    height: var(--selector-row-action-size, 1.5rem);
}

wa-dropdown-item:hover .row-menu-trigger,
.row-menu:focus-within .row-menu-trigger {
    opacity: 1;
}
</style>
