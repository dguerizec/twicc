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

import { computed, ref, watchEffect } from 'vue'
import ToolUseContent from '../ToolUseContent.vue'
import { useDataStore } from '../../../../../stores/data'
import { hasContent } from '../../../../../utils/parsedContent'

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

// Forwarded to ``ToolUseContent`` and reaches helper hooks that take a
// third options argument (e.g. ``getExpectedResultCount``). The wrapper
// type drives the "how many tool_results to wait for" decision because
// it disambiguates same-named tools (apply_patch JSON vs Freeform) and
// because MCP tools are only identifiable through the custom_tool_call
// wrapper + their ``mcp__`` name prefix.
const toolExtra = computed(() => ({ wrapperType: payload.value?.type ?? null }))

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

// Force-load the contents of every tool_result row paired with this
// call. Codex tool_results are DEBUG_ONLY items (event_msg.*_end +
// custom_tool_call_output / function_call_output), so by default the
// virtual scroller leaves them at ``content: null`` whenever the user
// hasn't scrolled them into view in DEBUG mode. The summary / header
// helpers (``getHeaderLabel``, ``getSummaryRendering``) need that
// content to surface the runtime's ``parsed_cmd`` / ``changes`` /
// ``aggregated_output``, and they fall back to a less precise local
// estimate when missing — that's the bug we're avoiding.
//
// Loading is gated on the wrapper being mounted and on the matching
// links having reached the store: the watchEffect re-runs once
// ``toolResultLineNums`` shows up, asks the store for the missing
// content lines (those with ``hasContent === false``) in a single
// request, and self-disables once everything is filled.
const dataStore = useDataStore()
const isLoadingResultItems = ref(false)
watchEffect(async () => {
    const tid = toolId.value
    if (!tid) return
    const toolState = dataStore.getToolState(props.sessionId, tid)
    const lineNums = toolState?.toolResultLineNums
    if (!Array.isArray(lineNums) || lineNums.length === 0) return

    const missing = []
    for (const ln of lineNums) {
        if (!Number.isInteger(ln) || ln < 1) continue
        const item = dataStore.getSessionItem(props.sessionId, ln)
        if (!item || !hasContent(item)) missing.push(ln)
    }
    if (missing.length === 0) return
    if (isLoadingResultItems.value) return

    isLoadingResultItems.value = true
    try {
        await dataStore.loadSessionItemsRanges(
            props.projectId,
            props.sessionId,
            missing,
            props.parentSessionId,
        )
    } finally {
        isLoadingResultItems.value = false
    }
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
        :extra="toolExtra"
    />
</template>
