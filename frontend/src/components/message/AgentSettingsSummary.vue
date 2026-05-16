<script setup>
// Compact one-line summary of the session's effective agent settings,
// prefixed by the session's provider (icon + label). Rendered inside the
// trigger button of the agent-settings popover — inherits typography /
// colour from the surrounding ``wa-button`` label part. The provider's
// helpers compute the actual parts list from the session's
// ``summaryState`` via ``getSummaryParts`` (a hook on
// ``BaseProviderHelpers``); this component just renders them.
import { computed } from 'vue'
import { getProviderIcon } from '../../providers'

const props = defineProps({
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
})

const helpers = computed(() => props.settings.providerHelpers.value)

const providerLabel = computed(() => helpers.value?.constructor.label ?? null)

const providerIcon = computed(() => getProviderIcon(helpers.value?.constructor.provider))

const parts = computed(() => {
    if (!helpers.value) return []
    return helpers.value.getSummaryParts(props.settings.summaryState.value) ?? []
})
</script>

<template>
    <span class="agent-settings-summary">
        <template v-if="providerLabel">
            <wa-icon
                v-if="providerIcon"
                auto-width
                family="brands"
                :name="providerIcon"
                class="provider-icon"
            ></wa-icon>
            <span>{{ providerLabel }}</span>
        </template>
        <template v-for="(part, i) in parts" :key="i">
            <span v-if="i || providerLabel"> · </span>
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

.provider-icon {
    margin-right: 0.25rem;
}
</style>
