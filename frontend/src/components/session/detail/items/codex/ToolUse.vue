<script setup>
/**
 * Codex tool_use wrapper.
 *
 * Codex packs every tool call into a single JSONL line (no nested
 * ``content[]`` like Claude). The shared ``ToolUseContent`` shell wants
 * ``{ name, input, toolId }`` — this wrapper pulls them out of the parsed
 * line so the dispatcher in ``SessionItem.vue`` only has to mount us with
 * the raw content.
 *
 * Two payload shapes contribute:
 *   - ``function_call``: the standard OpenAI form. ``arguments`` is a
 *     JSON-string that we parse into the input object.
 *   - ``custom_tool_call``: the freeform variant (apply_patch). ``input``
 *     is raw text — we wrap it in an object so the shell's JsonHumanView
 *     fallback shows it without surprise.
 */

import { computed } from 'vue'
import ToolUseContent from '../ToolUseContent.vue'

const props = defineProps({
    content: {
        type: Object,
        required: true,
    },
    projectId: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    parentSessionId: {
        type: String,
        default: null,
    },
    lineNum: {
        type: Number,
        required: true,
    },
})

const payload = computed(() => {
    const p = props.content?.payload
    return p && typeof p === 'object' ? p : null
})

const toolName = computed(() => payload.value?.name ?? '')
const toolId = computed(() => payload.value?.call_id ?? '')

const toolInput = computed(() => {
    const p = payload.value
    if (!p) return {}
    if (p.type === 'function_call') {
        const raw = p.arguments
        if (typeof raw !== 'string' || !raw) return {}
        try {
            const parsed = JSON.parse(raw)
            return parsed && typeof parsed === 'object' ? parsed : { _raw_arguments: raw }
        } catch {
            return { _raw_arguments: raw }
        }
    }
    if (p.type === 'custom_tool_call') {
        return { input: p.input ?? '' }
    }
    return {}
})
</script>

<template>
    <ToolUseContent
        v-if="payload"
        :name="toolName"
        :input="toolInput"
        :tool-id="toolId"
        :project-id="projectId"
        :session-id="sessionId"
        :parent-session-id="parentSessionId"
        :line-num="lineNum"
        :timestamp="content?.timestamp"
    />
</template>
