<script setup>
// Shared "state" indicator (icon + label) for a workflow run, a phase, or an
// agent. kind ∈ 'running' | 'completed' | 'interrupted' | 'pending'. 'interrupted'
// = a terminal non-success (e.g. a killed run / its frozen agents); there's no
// guessed 'failed' kind — the engine's failure vocabulary is unconfirmed.
// `label` overrides the default text — used to relay the engine's raw status
// verbatim on an interrupted run (e.g. "Killed").
defineProps({
    kind: { type: String, required: true },
    label: { type: String, default: null },
})
const LABELS = { running: 'Running', completed: 'Completed', interrupted: 'Interrupted', pending: 'Pending' }
</script>

<template>
    <span class="wf-state">
        <wa-spinner v-if="kind === 'running'" class="wf-state-icon"></wa-spinner>
        <wa-icon v-else-if="kind === 'interrupted'" name="circle-stop" class="wf-state-icon wf-state-interrupted"></wa-icon>
        <wa-icon v-else-if="kind === 'pending'" name="hourglass-start" class="wf-state-icon wf-state-pending"></wa-icon>
        <wa-icon v-else name="circle-check" class="wf-state-icon wf-state-done"></wa-icon>
        <span>{{ label || LABELS[kind] || kind }}</span>
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

.wf-state-interrupted {
    color: var(--wa-color-warning-50);
}

.wf-state-pending {
    color: var(--wa-color-text-quiet);
}

.wf-state-done {
    color: var(--wa-color-success-50);
}
</style>
