<script setup>
// Presentational renderer for a compact one-line agent-settings summary:
// the provider (icon + label) followed by the precomputed summary parts.
// Kept free of any session/composable knowledge so it can be reused both by
// the message-input summary (live ``useSessionAgentSettings`` state) and by
// the orchestration tree (static per-node bundles). Callers compute the
// ``parts`` via ``providerHelpers.getSummaryParts(state)`` and pass them in.
//
// ``markForced`` controls the dashed underline on parts that differ from the
// provider defaults: useful when a user is composing a session (see at a
// glance what they changed), pointless for sessions that spawned each other
// in an orchestration tree — those pass ``markForced=false`` to render every
// effective value plainly.
import { computed } from 'vue'
import { getProviderHelpers, getProviderIcon } from '../../providers'

const props = defineProps({
    provider: { type: String, default: null },
    // [{ text: string, forced: boolean }] — already resolved by the caller.
    parts: { type: Array, default: () => [] },
    markForced: { type: Boolean, default: true },
})

const providerLabel = computed(() =>
    props.provider ? (getProviderHelpers(props.provider)?.constructor.label ?? null) : null
)

const providerIcon = computed(() => getProviderIcon(props.provider))
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
            <span v-if="markForced && part.forced" class="setting-forced">{{ part.text }}</span>
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
