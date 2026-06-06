<script setup>
// ProjectBadge.vue - Displays a project color dot and display name
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'

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
    gap: {
        type: String,
        default: null,
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
const color = computed(() => project.value?.color || props.fallbackColor || null)
</script>

<template>
    <span class="project-badge" :style="gap ? { '--badge-gap': gap } : null">
        <span
            class="project-badge-dot"
            :style="color ? { '--dot-color': color } : null"
        ></span>
        <span class="project-badge-name">{{ displayName }}</span>
    </span>
</template>

<style scoped>
.project-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--badge-gap, var(--wa-space-xs));
    min-width: 0;
}

.project-badge-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.project-badge-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
</style>
