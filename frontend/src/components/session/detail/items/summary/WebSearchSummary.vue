<script setup>
/**
 * Summary line for a Web-search tool_use card.
 *
 * Accepts either a single ``query`` string (Claude Code's WebSearch
 * tool, OpenAI's ``web_search_call.action.query``) or an array of
 * queries (OpenAI's ``web_search_call.action.queries`` — present on
 * search variants that issue multiple queries at once). Strings render
 * inline; arrays render one query per line so multiple queries stay
 * legible without comma-noise.
 */
import { computed } from 'vue'

const props = defineProps({
    query: { type: [String, Array], required: true },
})

const isArray = computed(() => Array.isArray(props.query))

const display = computed(() => {
    if (isArray.value) {
        return props.query.filter((q) => typeof q === 'string' && q).join('\n')
    }
    return typeof props.query === 'string' ? props.query : ''
})
</script>

<template>
    <span class="items-details-summary-description websearch-summary" :class="{ 'websearch-summary-multiline': isArray }">{{ display }}</span>
</template>

<style scoped>
/* Preserve the ``\n`` separators when rendering an array of queries so
 * each query lands on its own line. Single-string usage stays unchanged
 * (the parent's default wrapping behaviour applies). */
.websearch-summary-multiline {
    white-space: pre-line;
}
</style>
