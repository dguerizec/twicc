<script setup>
// Shared "state" indicator (icon + label) for a workflow run, a phase, or an
// agent. kind ∈ 'running' | 'completed' | 'failed' | 'pending'.
defineProps({ kind: { type: String, required: true } })
const LABELS = { running: 'Running', completed: 'Completed', failed: 'Failed', pending: 'Pending' }
</script>

<template>
    <span class="wf-state">
        <wa-spinner v-if="kind === 'running'" class="wf-state-icon"></wa-spinner>
        <wa-icon v-else-if="kind === 'failed'" name="circle-xmark" class="wf-state-icon wf-state-failed"></wa-icon>
        <wa-icon v-else-if="kind === 'pending'" name="hourglass-start" class="wf-state-icon wf-state-pending"></wa-icon>
        <wa-icon v-else name="circle-check" class="wf-state-icon wf-state-done"></wa-icon>
        <span>{{ LABELS[kind] || kind }}</span>
    </span>
</template>

<style scoped>
.wf-state {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.wf-state-icon {
    font-size: 1.1em;
}

.wf-state-failed {
    color: var(--wa-color-danger-50);
}

.wf-state-pending {
    color: var(--wa-color-text-quiet);
}

.wf-state-done {
    color: var(--wa-color-success-50);
}
</style>
