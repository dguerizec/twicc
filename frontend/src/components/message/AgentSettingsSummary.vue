<script setup>
// Compact one-line summary of the session's effective agent settings.
// Rendered inside the trigger button of the agent-settings popover —
// inherits typography/colour from the surrounding ``wa-button`` label
// part. The provider's helpers compute the actual parts list from the
// session's ``summaryState`` via ``getSummaryParts`` (a hook on
// ``BaseProviderHelpers``); this component just renders them.
import { computed } from 'vue'

const props = defineProps({
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
})

const parts = computed(() => {
    const helpers = props.settings.providerHelpers.value
    if (!helpers) return []
    return helpers.getSummaryParts(props.settings.summaryState.value) ?? []
})
</script>

<template>
    <span class="agent-settings-summary">
        <template v-for="(part, i) in parts" :key="i">
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
