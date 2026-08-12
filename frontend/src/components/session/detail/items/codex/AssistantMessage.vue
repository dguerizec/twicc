<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import { splitProposedPlan } from '../../../../../providers/codex/proposedPlan'
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

// Codex Plan-mode final answers carry a ``<proposed_plan>`` block — see
// ``providers/codex/proposedPlan.js`` for the tag contract and the split
// semantics (streaming-tolerant). No block → plain assistant text, untouched.
const segments = computed(() => splitProposedPlan(props.text))

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
