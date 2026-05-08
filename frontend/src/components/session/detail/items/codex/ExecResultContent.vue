<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

/**
 * Codex exec_command Result body.
 *
 * Mirrors Claude Code's ``BashResultContent`` — the input is the
 * ``aggregated_output`` field carried by an
 * ``event_msg.exec_command_end`` payload (combined stdout/stderr,
 * already truncated by Codex if too long). We wrap it in a fenced
 * code block (no language so MarkdownContent's Shiki renders it
 * monospace, no syntax coloring) and let MarkdownContent handle
 * the toolbar, raw view and overflow scroll.
 */

const props = defineProps({
    // Single ``event_msg.exec_command_end`` payload chosen by the helper.
    // Always carries ``aggregated_output`` (string).
    result: { type: Object, required: true },
})

const markdownSource = computed(() => {
    const out = props.result?.aggregated_output
    if (typeof out !== 'string' || !out) return null
    return '```\n' + out + '\n```'
})
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" />
</template>
