<script setup>
// ProjectBadge.vue - Displays a project color dot and display name
import { computed, toRef } from 'vue'
import { useDataStore } from '../../stores/data'
import { projectPathTitle } from '../../utils/projectName'
import { useProjectMark } from '../../composables/useProjectMark'
import ProjectMark from './ProjectMark.vue'
import ProjectMissingDirectoryIcon from './ProjectMissingDirectoryIcon.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    useDirectoryForUnnamed: {
        type: Boolean,
        default: false,
    },
    // Explicit label override. When set, it replaces the computed display name
    // (the color dot still comes from the project). Used e.g. for worktree
    // entries, which show their name or just their final folder name.
    label: {
        type: String,
        default: null,
    },
    // Fallback dot color used when the project itself has no color. Used e.g.
    // for worktree entries, which inherit their main repository's color.
    fallbackColor: {
        type: String,
        default: null,
    },
    // Explicit dot color override. When non-null (including an empty string,
    // meaning "no color"), it replaces the project's stored color. Used for live
    // previews where the badge must reflect an unsaved color choice. `null` =
    // not overriding (fall back to the project's stored color).
    colorOverride: {
        type: String,
        default: null,
    },
    gap: {
        type: String,
        default: null,
    },
    // Opt-in: mark the badge when the project's working directory is gone.
    // Deliberately NOT the default — the badge appears in dozens of places and
    // the mark would become noise. Only the sidebar project selector turns it
    // on: it is the one list where the state is otherwise invisible and where
    // the row is the way to reach the project home, which explains it in full.
    flagMissingDirectory: {
        type: Boolean,
        default: false,
    },
})

const store = useDataStore()

const project = computed(() => store.getProject(props.projectId))
const displayName = computed(() => {
    if (props.label != null) {
        return props.label
    }
    if (props.useDirectoryForUnnamed && project.value && !project.value.name) {
        return project.value.directory || store.getProjectDisplayName(props.projectId)
    }
    return store.getProjectDisplayName(props.projectId)
})
// For unnamed projects (shown with just their final folder name), reveal the
// full directory path on hover. Suppressed when the displayed name already is
// the full path (e.g. `useDirectoryForUnnamed`).
const nameTitle = computed(() => {
    const path = projectPathTitle(project.value)
    return path && path !== displayName.value ? path : null
})
const { iconUrl, dotColor } = useProjectMark(toRef(props, 'projectId'), {
    colorOverride: toRef(props, 'colorOverride'),
    fallbackColor: toRef(props, 'fallbackColor'),
})
// Whether to flag this project as untrusted (effective trust ≠ trusted, i.e.
// explicitly untrusted OR unknown). Resolved from the store's cached set.
const untrusted = computed(() => store.untrustedProjectIds.has(props.projectId))
</script>

<template>
    <span class="project-badge" :style="gap ? { '--badge-gap': gap } : null">
        <ProjectMark :icon-url="iconUrl" :color="dotColor" />
        <span class="project-badge-name" :title="nameTitle">{{ displayName }}</span>
        <ProjectMissingDirectoryIcon v-if="flagMissingDirectory" :project-id="projectId" />
        <wa-icon
            v-if="untrusted"
            name="lock"
            label="Untrusted project"
            title="This project is not trusted"
            class="project-badge-trust"
        ></wa-icon>
    </span>
</template>

<style scoped>
.project-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--badge-gap, var(--wa-space-xs));
    min-width: 0;
}

.project-badge-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* Untrusted marker: normal text colour at low opacity — faint, and adapts to
   light/dark and every theme on its own (quieter than --wa-color-text-quiet). */
.project-badge-trust {
    flex-shrink: 0;
    color: var(--wa-color-text-normal);
    opacity: 0.2;
    font-size: 0.85em;
}

/* :deep — ProjectMissingDirectoryIcon has two root nodes (icon + tooltip), so
   Vue does not stamp this component's scope id on its icon. */
.project-badge :deep(.missing-directory-icon) {
    flex-shrink: 0;
    font-size: 0.85em;
}
</style>
