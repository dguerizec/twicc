<script setup>
// WorktreeBadge.vue — display label for the *current* project when it is a git
// worktree. Shown identically in the sidebar selector trigger and the project
// home header:
//
//   [color dot] <main repo name> <code-branch icon> <worktree folder>
//
// The color dot uses the worktree's own color, falling back to its main
// repository's. The folder is the worktree's own name if it has one, else just
// the final folder name of its directory (see worktreeLabel). When the parent
// project can't be resolved, only the folder is shown (degrades to a plain
// badge). The leading dot can be suppressed (`:dot="false"`) when the caller
// already draws its own (e.g. the selector trigger). When `parentLink` is set,
// the main repo name becomes a link to that project's home.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { worktreeLabel } from '../../utils/worktree'

const props = defineProps({
    projectId: { type: String, required: true },
    // Whether to render the leading color dot.
    dot: { type: Boolean, default: true },
    // When true, the main repository name links to that project's home.
    parentLink: { type: Boolean, default: false },
    // Explicit override for the worktree folder label. When non-null it replaces
    // the computed label. Used for live previews reflecting an unsaved name.
    folderOverride: { type: String, default: null },
    // Explicit dot color override for the worktree's own color. When non-null
    // (including an empty string, meaning "no own color → inherit the parent's")
    // it replaces the worktree's stored color. Used for live previews.
    colorOverride: { type: String, default: null },
    gap: { type: String, default: null },
})

const store = useDataStore()
const project = computed(() => store.getProject(props.projectId))
const parent = computed(() => {
    const pid = project.value?.worktree_of
    return pid ? store.getProject(pid) : null
})
const parentName = computed(() => parent.value ? store.getProjectDisplayName(parent.value.id) : '')
const parentRoute = computed(() => parent.value ? { name: 'project', params: { projectId: parent.value.id } } : null)
const folder = computed(() =>
    props.folderOverride !== null
        ? props.folderOverride
        : worktreeLabel(project.value) || store.getProjectDisplayName(props.projectId)
)
const color = computed(() => {
    const own = props.colorOverride !== null ? props.colorOverride : project.value?.color
    return own || parent.value?.color || null
})
</script>

<template>
    <span class="worktree-badge" :style="gap ? { '--badge-gap': gap } : null">
        <span
            v-if="dot"
            class="worktree-badge-dot"
            :style="color ? { '--dot-color': color } : null"
        ></span>
        <template v-if="parentName">
            <RouterLink
                v-if="parentLink && parentRoute"
                :to="parentRoute"
                class="worktree-badge-name worktree-badge-parent-link"
                @click.stop
            >{{ parentName }}</RouterLink>
            <span v-else class="worktree-badge-name">{{ parentName }}</span>
            <wa-icon name="code-branch" auto-width class="worktree-badge-sep"></wa-icon>
        </template>
        <span class="worktree-badge-name">{{ folder }}</span>
    </span>
</template>

<style scoped>
.worktree-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--badge-gap, var(--wa-space-xs));
    min-width: 0;
}

.worktree-badge-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.worktree-badge-sep {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: 0.85em;
}

.worktree-badge-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.worktree-badge-parent-link {
    color: inherit;
    text-decoration: none;
    cursor: pointer;
}

.worktree-badge-parent-link:hover {
    color: var(--wa-color-brand-text);
    text-decoration: underline;
}
</style>
