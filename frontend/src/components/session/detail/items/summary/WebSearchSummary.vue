<script setup>
/**
 * Summary line for a Web-search tool_use card.
 *
 * Accepts either a single ``query`` string (Claude Code's WebSearch
 * tool, OpenAI's ``web_search_call.action.query``) or an array of
 * queries (OpenAI's ``web_search_call.action.queries`` — present on
 * search variants that issue multiple queries at once). Arrays are
 * rendered as a comma-separated list; strings pass through unchanged.
 */
import { computed } from 'vue'

const props = defineProps({
    query: { type: [String, Array], required: true },
})

const display = computed(() => {
    if (Array.isArray(props.query)) {
        return props.query.filter((q) => typeof q === 'string' && q).join(', ')
    }
    return typeof props.query === 'string' ? props.query : ''
})
</script>

<template>
    <span class="items-details-summary-description">{{ display }}</span>
</template>
