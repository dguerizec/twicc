<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import Reasoning from './Reasoning.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'

const props = defineProps({
    // Parsed JSONL line. Two shapes are supported:
    //  - Real Codex line: ``{ timestamp, type: 'event_msg', payload: { type:
    //    'user_message' | 'agent_message', message: string, ... } }``
    //  - Synthetic placeholder injected by the store (optimistic user
    //    message, or STARTING / WORKING assistant message, or live
    //    streaming text/thinking block). These carry ``syntheticKind``
    //    at the top level and rely on the dispatch below.
    data: {
        type: Object,
        required: true
    },
    // ItemKind value driving the user/assistant dispatch.
    kind: {
        type: String,
        required: true,
        validator: (value) => ['user_message', 'assistant_message'].includes(value)
    },
    // Forwarded to ``WorkingAssistantMessage`` so it can derive the provider
    // label and the session's base directory for tool summaries.
    sessionId: {
        type: String,
        required: true
    },
    // Used by the streaming ``Reasoning`` placeholder to build a stable
    // persisted open/closed key on the synthetic negative line number.
    lineNum: {
        type: Number,
        required: true
    }
})

const isStartingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind
)

const isWorkingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind
)

const isStreamingBlock = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind
)

// A streaming placeholder paints a single content block at index 0;
// inspect its ``type`` to route ``thinking`` blocks to ``Reasoning`` and
// regular ``text`` blocks to ``AssistantMessage`` (same dispatch Claude
// does inside its ``ContentList``).
const streamingBlockType = computed(() => {
    if (!isStreamingBlock.value) return null
    return props.data?.message?.content?.[0]?.type ?? null
})

// Plain text source for the ``AssistantMessage`` / ``UserMessage`` path.
// Two shapes feed in:
//
//  - Real Codex line: the flat string sits under ``payload.message``
//    (event_msg.user_message / event_msg.agent_message) — no content
//    array, no nested blocks.
//  - Streaming text placeholder: the store paints the live SDK delta into
//    a Claude-style content array under ``message.content`` so the same
//    rendering plumbing can carry both providers.
const text = computed(() => {
    if (isStreamingBlock.value) {
        const content = props.data?.message?.content
        if (Array.isArray(content) && content.length > 0) {
            return content[0].text || ''
        }
        return ''
    }
    return props.data?.payload?.message || ''
})

// Attached images on a user_message line. Codex CLI persists them in
// ``payload.images`` as full ``data:image/...;base64,...`` URLs (one per
// attachment). ``local_images`` / ``text_elements`` are protocol fields
// the CLI uses when input is referenced by path or annotated with byte
// ranges — neither shape is produced by TwiCC's send pipeline today, so
// only the ``images`` array is consumed here.
const images = computed(() => {
    if (props.kind !== 'user_message') return []
    const list = props.data?.payload?.images
    return Array.isArray(list) ? list : []
})
</script>

<template>
    <WorkingAssistantMessage
        v-if="isStartingAssistantMessage"
        label="starting"
        process-state="starting"
        :session-id="sessionId"
    />
    <WorkingAssistantMessage
        v-else-if="isWorkingAssistantMessage"
        :session-id="sessionId"
    />
    <Reasoning
        v-else-if="streamingBlockType === 'thinking'"
        :data="data"
        :session-id="sessionId"
        :line-num="lineNum"
    />
    <UserMessage v-else-if="kind === 'user_message'" :text="text" :images="images" />
    <AssistantMessage v-else-if="kind === 'assistant_message'" :text="text" />
</template>
