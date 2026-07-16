<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import TextContent from '../TextContent.vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    text: {
        type: String,
        required: true
    },
    // Both feed the persisted open/closed state of the proposed-plan
    // details (same pattern as ``Reasoning``).
    sessionId: {
        type: String,
        required: true
    },
    lineNum: {
        type: Number,
        required: true
    }
})

// Codex Plan collaboration mode wraps its final plan in literal
// ``<proposed_plan>`` / ``</proposed_plan>`` tags so clients can render it
// specially. The mode's built-in instructions make the shape a stable
// contract: exact tags (never translated), each on its own line, markdown
// inside, at most one block per turn, possibly surrounded by ordinary
// assistant text. Split the message around the block; the closing tag is
// optional so a streaming placeholder already shows the details while the
// plan text is still growing (the block always ends the message in that
// case). No opening tag → plain assistant text, untouched.
const OPEN_TAG_RE = /(?:^|\n)[ \t]*<proposed_plan>[ \t]*(?:\n|$)/
const CLOSE_TAG_RE = /(?:^|\n)[ \t]*<\/proposed_plan>[ \t]*(?:\n|$)/

const segments = computed(() => {
    const openMatch = props.text.match(OPEN_TAG_RE)
    if (!openMatch) return null
    const before = props.text.slice(0, openMatch.index)
    const rest = props.text.slice(openMatch.index + openMatch[0].length)
    const closeMatch = rest.match(CLOSE_TAG_RE)
    const plan = closeMatch ? rest.slice(0, closeMatch.index) : rest
    const after = closeMatch ? rest.slice(closeMatch.index + closeMatch[0].length) : ''
    return { before: before.trim(), plan: plan.trim(), after: after.trim() }
})

const dataStore = useDataStore()

// Persisted open/closed state, mirroring ``Reasoning`` — but INVERTED: the
// plan is the turn's deliverable, so the details default OPEN. The store
// only persists ``true`` values (a ``false`` deletes the key), so what is
// recorded is the user's explicit CLOSE, and a missing entry means open.
const closedKey = computed(() => `plan-closed:${props.lineNum}`)

const isOpen = ref(!dataStore.isDetailOpen(props.sessionId, closedKey.value))

// Skip the open animation when mounting already-open (virtual scroller
// restoration — which, defaulting open, is every mount unless the user
// closed it). Same pattern as ToolUseContent / Reasoning.
const instantOpen = ref(isOpen.value)

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onShow() {
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, closedKey.value, false)
}

function onHide() {
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, closedKey.value, true)
}
</script>

<template>
    <div v-if="segments" class="assistant-message-with-plan">
        <TextContent v-if="segments.before" :text="segments.before" role="assistant" />
        <wa-details
            :open="isOpen"
            :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null"
            class="item-details proposed-plan"
            icon-placement="start"
            @wa-show.self="onShow"
            @wa-hide.self="onHide"
        >
            <span slot="summary" class="items-details-summary">
                <strong class="items-details-summary-name">Proposed plan</strong>
            </span>
            <div v-if="isOpen" class="proposed-plan-body">
                <MarkdownContent :source="segments.plan" />
            </div>
        </wa-details>
        <TextContent v-if="segments.after" :text="segments.after" role="assistant" />
    </div>
    <TextContent v-else :text="text" role="assistant" />
</template>

<style scoped>
.proposed-plan-body {
    word-break: break-word;
}
</style>
