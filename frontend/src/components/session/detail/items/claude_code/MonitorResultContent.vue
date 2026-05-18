<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    aggregatedOutput: {
        type: String,
        required: true,
    },
    isTerminated: {
        type: Boolean,
        required: true,
    },
})

const markdownSource = computed(() => {
    if (props.aggregatedOutput) {
        return '```\n' + props.aggregatedOutput + '\n```'
    }
    if (!props.isTerminated) {
        return '```\nWaiting for monitor events…\n```'
    }
    return null
})

// Suppress the copy / raw-toggle toolbar on the "Waiting…" placeholder —
// it's the only call site in the codebase that exercises show-toolbar=false,
// because Monitor uses the placeholder frame to keep the body's vertical
// footprint stable across the running→completed transition (Bash never
// renders an empty body, so it never needs the opt-out).
const showToolbar = computed(() => !!props.aggregatedOutput)
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" :show-toolbar="showToolbar" />
    <div v-else class="monitor-output__no-events">(no events)</div>
</template>

<style scoped>
.monitor-output__no-events {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
