<script setup>
/**
 * CodeCommentsIndicator - Shows a comment icon when there are unsent code comments.
 *
 * Two usage modes:
 * - Pass `projectIds` to aggregate counts from the store (project/workspace level).
 * - Pass `count` directly for finer granularities (session, source, file level).
 *
 * Wrapped in a single root <span> so it can be placed in web component slots
 * (e.g. slot="end" on wa-button).
 */
import { computed, useId } from 'vue'
import { useCodeCommentsStore } from '../../stores/codeComments'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    /**
     * List of project IDs to count code comments for.
     * Mutually exclusive with `count`.
     */
    projectIds: {
        type: Array,
        default: null,
    },
    /**
     * Direct count of code comments (for finer granularities).
     * Mutually exclusive with `projectIds`.
     */
    count: {
        type: Number,
        default: null,
    },
    /**
     * Whether to show a tooltip with the count.
     * Disable in compact contexts like dropdown options.
     */
    showTooltip: {
        type: Boolean,
        default: true,
    },
})

const codeCommentsStore = useCodeCommentsStore()

const effectiveCount = computed(() => {
    if (props.count !== null) return props.count
    if (props.projectIds) return codeCommentsStore.countByProjects(props.projectIds)
    return 0
})

const tooltipText = computed(() => {
    const n = effectiveCount.value
    return n === 1
        ? '1 code comment not yet sent'
        : `${n} code comments not yet sent`
})

const indicatorId = useId()
</script>

<template>
    <span v-if="effectiveCount > 0" class="code-comments-indicator">
        <wa-icon :id="indicatorId" name="comment" variant="regular"></wa-icon>
        <AppTooltip v-if="showTooltip" :for="indicatorId">{{ tooltipText }}</AppTooltip>
    </span>
</template>

<style scoped>
.code-comments-indicator {
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-brand);
}
</style>
