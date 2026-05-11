<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'

const props = defineProps({
    // Parsed JSONL line. Two shapes are supported:
    //  - Real Codex line: ``{ timestamp, type: 'event_msg', payload: { type:
    //    'user_message' | 'agent_message', message: string, ... } }``
    //  - Synthetic placeholder injected by the store (optimistic user
    //    message, or STARTING / WORKING assistant message). These carry
    //    ``syntheticKind`` at the top level and rely on the dispatch below.
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
    }
})

const isStartingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind
)

const isWorkingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind
)

// V1: Codex stores the human input / agent reply as a flat string in
// payload.message — no content array, no nested blocks. Images on
// user_message events (data.payload.images) are out of scope for now.
const text = computed(() => props.data?.payload?.message || '')
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
    <UserMessage v-else-if="kind === 'user_message'" :text="text" />
    <AssistantMessage v-else-if="kind === 'assistant_message'" :text="text" />
</template>
