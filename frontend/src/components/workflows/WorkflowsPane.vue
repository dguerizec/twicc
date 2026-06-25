<script setup>
// Pane for a session's Claude Code workflows. The Workflows tab is only present
// when has_workflows is true (a wf_*.json exists at the root of the session's
// <session_id>/workflows/ folder). Empty for now — the list of workflow runs
// will be rendered here in a later step. The props mirror the other tool panes
// so wiring the future list (fetch keyed by session id, refetch on
// (re)activation) needs no SessionView change.
defineProps({
    sessionId: { type: String, required: true },
    // True while the Workflows tab is the shown tab in its region.
    active: { type: Boolean, default: false },
})
</script>

<template>
    <div class="workflows-pane">
        <!-- Workflow list goes here (later step). Intentionally empty for now. -->
    </div>
</template>

<style scoped>
.workflows-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}
</style>
