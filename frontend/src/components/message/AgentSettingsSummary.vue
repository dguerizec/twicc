<script setup>
// Compact one-line summary of the session's effective agent settings,
// prefixed by the session's provider (icon + label). Rendered inside the
// trigger button of the agent-settings popover — inherits typography /
// colour from the surrounding ``wa-button`` label part. The provider's
// helpers compute the actual parts list from the session's
// ``summaryState`` via ``getSummaryParts`` (a hook on
// ``BaseProviderHelpers``); the shared ``AgentSettingsSummaryView`` renders
// them. The default ``markForced`` underlines values that differ from the
// user's defaults so they see at a glance what they changed.
import { computed } from 'vue'
import AgentSettingsSummaryView from './AgentSettingsSummaryView.vue'

const props = defineProps({
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
})

const helpers = computed(() => props.settings.providerHelpers.value)

const provider = computed(() => helpers.value?.constructor.provider ?? null)

const parts = computed(() => {
    if (!helpers.value) return []
    return helpers.value.getSummaryParts(props.settings.summaryState.value) ?? []
})
</script>

<template>
    <AgentSettingsSummaryView :provider="provider" :parts="parts" />
</template>
