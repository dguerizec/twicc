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
import { computed, toRef } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { worktreeLabel } from '../../utils/worktree'
import { projectPathTitle } from '../../utils/projectName'
import { useProjectMark } from '../../composables/useProjectMark'
import ProjectMark from './ProjectMark.vue'

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
// Full directory paths shown on hover for unnamed projects (the main repo name
// and/or the worktree folder are otherwise just final folder names). Suppressed
// for the folder during a live preview (`folderOverride`).
const parentTitle = computed(() => projectPathTitle(parent.value))
const folderTitle = computed(() => {
    if (props.folderOverride !== null) {
        return null
    }
    const path = projectPathTitle(project.value)
    return path && path !== folder.value ? path : null
})
const { iconUrl, dotColor } = useProjectMark(toRef(props, 'projectId'), {
    colorOverride: toRef(props, 'colorOverride'),
})
// Flag the worktree as untrusted (effective trust ≠ trusted). The resolver
// already inherits the main repo's trust through `worktree_of`.
const untrusted = computed(() => store.untrustedProjectIds.has(props.projectId))
</script>

<template>
    <span class="worktree-badge" :style="gap ? { '--badge-gap': gap } : null">
        <ProjectMark v-if="dot" :icon-url="iconUrl" :color="dotColor" />
        <template v-if="parentName">
            <RouterLink
                v-if="parentLink && parentRoute"
                :to="parentRoute"
                class="worktree-badge-name worktree-badge-parent-link"
                :title="parentTitle"
                @click.stop
            >{{ parentName }}</RouterLink>
            <span v-else class="worktree-badge-name" :title="parentTitle">{{ parentName }}</span>
            <wa-icon name="code-branch" auto-width class="worktree-badge-sep"></wa-icon>
        </template>
        <span class="worktree-badge-name" :title="folderTitle">{{ folder }}</span>
        <wa-icon
            v-if="untrusted"
            name="lock"
            label="Untrusted project"
            title="This project is not trusted"
            class="worktree-badge-trust"
        ></wa-icon>
    </span>
</template>

<style scoped>
.worktree-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--badge-gap, var(--wa-space-xs));
    min-width: 0;
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

/* Untrusted marker: normal text colour at low opacity — faint, and adapts to
   light/dark and every theme on its own (quieter than --wa-color-text-quiet). */
.worktree-badge-trust {
    flex-shrink: 0;
    color: var(--wa-color-text-normal);
    opacity: 0.2;
    font-size: 0.85em;
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
