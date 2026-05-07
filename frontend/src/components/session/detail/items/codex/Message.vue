<script setup>
import { computed } from 'vue'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'

const props = defineProps({
    // Parsed JSONL line: { timestamp, type: "event_msg", payload: { type: "user_message" | "agent_message", message: string, ... } }
    data: {
        type: Object,
        required: true
    },
    // ItemKind value driving the user/assistant dispatch.
    kind: {
        type: String,
        required: true,
        validator: (value) => ['user_message', 'assistant_message'].includes(value)
    }
})

// V1: Codex stores the human input / agent reply as a flat string in
// payload.message — no content array, no nested blocks. Images on
// user_message events (data.payload.images) are out of scope for now.
const text = computed(() => props.data?.payload?.message || '')
</script>

<template>
    <UserMessage v-if="kind === 'user_message'" :text="text" />
    <AssistantMessage v-else-if="kind === 'assistant_message'" :text="text" />
</template>
