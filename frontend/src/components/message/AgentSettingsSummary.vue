<script setup>
// Compact one-line summary of the session's effective agent settings.
// Rendered inside the trigger button of the agent-settings popover —
// inherits typography/colour from the surrounding ``wa-button`` label
// part. Content is provider-aware: the parts come from
// ``useSessionAgentSettings`` which routes label/choice lookups through
// the helpers of the session's provider.
defineProps({
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
})
</script>

<template>
    <span class="agent-settings-summary">
        <template v-for="(part, i) in settings.settingsSummaryParts.value" :key="i">
            <span v-if="i"> · </span>
            <span v-if="part.forced" class="setting-forced">{{ part.text }}</span>
            <template v-else>{{ part.text }}</template>
        </template>
    </span>
</template>

<style scoped>
.setting-forced {
    text-decoration: underline dashed;
    text-underline-offset: 3px;
}
</style>
