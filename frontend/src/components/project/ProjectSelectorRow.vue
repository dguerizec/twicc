<script setup>
// ProjectSelectorRow.vue — one project entry in the sidebar project selector
// dropdown. Single source of truth for a project line: the selection check, the
// project badge (color dot + name) and the end-of-line state indicators
// (code comments + aggregated process/unread). Used for both normal projects
// and worktree entries — a worktree is simply the same row at a deeper `depth`,
// with a `label` (its name or just its final folder name) and a `fallbackColor`
// (its main repo's color). Keeping a single row component guarantees worktrees
// stay in lockstep with normal projects for every state indicator.
import ProjectBadge from './ProjectBadge.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'

defineProps({
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
</style>
