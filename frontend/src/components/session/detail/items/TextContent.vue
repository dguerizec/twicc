<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../ui/MarkdownContent.vue'
import { commandToText } from '../../../../utils/command'
import { LEADING_SLASH_COMMAND_RE } from '../../../../utils/markdown'

const props = defineProps({
    text: {
        type: String,
        required: true
    },
    // Also rendered as an attribute on the root element (styling hook).
    role: {
        type: String,
        default: null
    }
})

const displayText = computed(() => {
    const trimmed = props.text.trim()
    return commandToText(trimmed) ?? trimmed
})

// User messages starting with a slash command (a real command converted by
// commandToText, or free text typed with a leading /word) get the command
// rendered as a tag.
const tagSlashCommand = computed(() =>
    props.role === 'user' && LEADING_SLASH_COMMAND_RE.test(displayText.value)
)
</script>

<template>
    <div class="text-content" :role="role">
        <MarkdownContent :source="displayText" :tag-slash-command="tagSlashCommand" />
    </div>
</template>

<style scoped>
.text-content {
    word-break: break-word;
    font-family: var(--wa-font-sans);
}

/* In user messages, code blocks should wrap instead of scrolling horizontally,
   so that the full content is visible (and selectable for text comments). */
.text-content[role="user"] :deep(.markdown-body pre) {
    &, & code {
        white-space: pre-wrap;
        word-wrap: break-word;
    }
}

/* Leading /command of a user message, rendered as a tag (see the
   slash_command_tag rule in utils/markdown.js). */
.text-content :deep(.slash-command-tag) {
    display: inline-block;
    padding: 0.05em 0.45em;
    /* The source keeps the space after the command (copy fidelity), so only a
       sliver of extra margin is needed next to the chip's padding. */
    margin-right: 0.1em;
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-brand-fill-quiet);
    border: 1px solid var(--wa-color-brand-border-quiet);
    color: var(--wa-color-brand-on-quiet);
    font-family: var(--wa-font-family-code);
    font-size: 0.875em;
}
</style>
