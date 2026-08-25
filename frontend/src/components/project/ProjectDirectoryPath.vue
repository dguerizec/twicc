<script setup>
/**
 * ProjectDirectoryPath - a project's working directory, flagged when it is gone.
 *
 * Renders the path as plain text, and turns it into a warning (icon +
 * strikethrough) when the project is stale, so the state reads on the value it
 * is about rather than as a detached banner. See ProjectMissingDirectoryIcon
 * for what "stale" means and why it needs a manual re-check.
 *
 * Inherits font-size and base color from its container, so every list, card and
 * header keeps its own typography.
 */
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import ProjectMissingDirectoryIcon from './ProjectMissingDirectoryIcon.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const store = useDataStore()
const project = computed(() => store.getProject(props.projectId))
const directory = computed(() => project.value?.directory || '')
const missing = computed(() => !!project.value?.stale)
</script>

<template>
    <span class="directory-path" :class="{ 'is-missing': missing }">
        <ProjectMissingDirectoryIcon :project-id="projectId" />
        <span class="directory-path-text">{{ directory }}</span>
    </span>
</template>

<style scoped>
.directory-path {
    word-break: break-all;
}

.directory-path.is-missing {
    color: var(--wa-color-warning-on-quiet);
}

.directory-path.is-missing .directory-path-text {
    text-decoration: line-through;
    text-decoration-color: color-mix(in oklab, currentColor 50%, transparent);
}

/* :deep — ProjectMissingDirectoryIcon has two root nodes, so Vue does not stamp
   this component's scope id on its icon. */
.directory-path :deep(.missing-directory-icon) {
    margin-inline-end: var(--wa-space-2xs);
    vertical-align: -0.1em;
}
</style>
