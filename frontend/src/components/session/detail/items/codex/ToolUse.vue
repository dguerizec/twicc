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
 * Four payload shapes contribute:
 *   - ``function_call``: the standard OpenAI form. ``arguments`` is a
 *     JSON-string that we parse into the input object.
 *   - ``custom_tool_call``: the freeform variant (apply_patch). ``input``
 *     is raw text — we wrap it in an object so the shell's JsonHumanView
 *     fallback shows it without surprise.
 *   - ``local_shell_call``: the native shell tool exposed by the Responses
 *     API. No ``name`` field — the sub_type is the tool name. The argv
 *     ships through ``payload.action`` (which is itself a tagged enum,
 *     today always ``{type:"exec", command, timeout_ms, ...}``); we hand
 *     it over verbatim so ``parseCommand`` keeps the array form (it
 *     unwraps ``bash -lc <script>`` and classifies Read / Grep / List),
 *     and JsonHumanView auto-joins the array for the bash code-block.
 *   - ``web_search_call``: the native web-search / web-fetch tool. No
 *     ``name`` field, no ``call_id``, no result. Its ``payload.action``
 *     is a tagged enum (``{type:"search", query|queries}`` /
 *     ``{type:"open_page", url}`` / ``{type:"find_in_page", url, pattern}``)
 *     forwarded verbatim — toolHelpers picks the header label and the
 *     summary component off ``action.type``.
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

const toolName = computed(() => {
    const p = payload.value
    if (!p) return ''
    // MCP tools land as ``function_call`` with a ``namespace`` field
    // carrying the ``mcp__server__app`` prefix and a ``name`` carrying
    // only the leaf tool name (often starting with an underscore like
    // ``"_search_repositories"``). Combine them so the canonical
    // tool_name has the same ``mcp__server__app__tool`` shape as
    // Claude Code's MCP tools — that's what powers the
    // ``startsWith("mcp__")`` checks in the helpers and what the
    // shell header formatter splits on ``__`` to display.
    if (p.namespace && typeof p.namespace === 'string' && typeof p.name === 'string') {
        return `${p.namespace}__${p.name}`
    }
    // ``local_shell_call`` and other native tool-call shapes don't carry
    // a ``name`` field — fall back to the payload sub_type which serves
    // as the canonical tool name in those cases.
    return p.name ?? p.type ?? ''
})
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
    if (p.type === 'local_shell_call') {
        // The native ``local_shell`` tool ships its argv-shaped command
        // through ``payload.action`` instead of an ``arguments`` JSON
        // string. Forward the action object as-is — the ``command`` array
        // is preserved so ``parseCommand`` can unwrap ``bash -lc <script>``
        // and classify Read / Grep / List for the header label; the
        // JsonHumanView ``string-code`` override flattens the array to a
        // bash line for the body display.
        return p.action && typeof p.action === 'object' ? p.action : {}
    }
    if (p.type === 'web_search_call') {
        // Same pattern as ``local_shell_call``: ``payload.action`` is a
        // tagged enum (search / open_page / find_in_page) — we forward
        // it as-is so the helpers can branch on ``action.type`` for
        // the header label (WebSearch / WebFetch) and the summary
        // component (query string/array, or URL link).
        return p.action && typeof p.action === 'object' ? p.action : {}
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
