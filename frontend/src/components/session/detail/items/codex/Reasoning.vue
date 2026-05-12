<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    // Two shapes flow through this component:
    //
    //  - Real Codex JSONL line:
    //      { type: 'response_item', payload: { type: 'reasoning',
    //        summary: [{ type: 'summary_text', text }, ...], ... } }
    //
    //  - Synthetic streaming placeholder (built by ``computeVisualItems``
    //    in the data store when a ``stream_block_*`` thinking burst is in
    //    flight):
    //      { type: 'assistant', syntheticKind: 'streaming_block',
    //        message: { content: [{ type: 'thinking', thinking, streaming }] } }
    //
    // Both shapes are reduced to ``text`` (a single concatenated markdown
    // source) and ``streaming`` (whether to show a spinner in the summary
    // bar). Only the synthetic shape ever produces ``streaming === true``.
    data: {
        type: Object,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    // Reasoning items are mono-block in the JSONL shape (one summary list
    // per line) and ``streamBlockStart`` paints synthetic items at a
    // unique negative ``lineNum`` per block, so a ``:0`` suffix is enough
    // to disambiguate.
    lineNum: {
        type: Number,
        required: true,
    },
})

const dataStore = useDataStore()

const detailKey = computed(() => `line:${props.lineNum}:0`)

// Extract ``text`` + ``streaming`` from whichever shape the parent passed.
// JSONL: read every ``summary_text`` entry. Synthetic: read every
// ``thinking`` content block. In both cases we join with ``\n\n`` so the
// markdown renderer treats successive entries as separate paragraphs.
const extracted = computed(() => {
    const summary = props.data?.payload?.summary
    if (Array.isArray(summary)) {
        const text = summary
            .filter(s => s?.type === 'summary_text' && typeof s.text === 'string' && s.text.trim())
            .map(s => s.text)
            .join('\n\n')
        return { text, streaming: false }
    }
    const blocks = props.data?.message?.content
    if (Array.isArray(blocks)) {
        const text = blocks
            .filter(b => b?.type === 'thinking' && typeof b.thinking === 'string' && b.thinking.trim())
            .map(b => b.thinking)
            .join('\n\n')
        const streaming = blocks.some(b => b?.type === 'thinking' && b.streaming === true)
        return { text, streaming }
    }
    return { text: '', streaming: false }
})

const text = computed(() => extracted.value.text)
const streaming = computed(() => extracted.value.streaming)

// Lazy rendering + persisted open state, mirrored from
// ``claude_code/ThinkingContent.vue``. Initialized from the store so the
// virtual scroller's mount/unmount cycle doesn't reset the user's choice.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, detailKey.value))

// Skip the open animation when mounting already-open (virtual scroller
// restoration). Same pattern as ToolUseContent / ThinkingContent.
const instantOpen = ref(isOpen.value)

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onShow() {
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, detailKey.value, true)
}

function onHide() {
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, detailKey.value, false)
}
</script>

<template>
    <wa-details
        :open="isOpen"
        :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null"
        class="item-details reasoning-content"
        icon-placement="start"
        @wa-show="onShow"
        @wa-hide="onHide"
    >
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Reasoning</strong>
            <wa-spinner v-if="streaming"></wa-spinner>
        </span>
        <div v-if="isOpen" class="reasoning-body">
            <MarkdownContent :source="text" />
        </div>
    </wa-details>
</template>

<style scoped>
wa-details {
    &::part(content) {
        padding-top: 0;
    }

    .items-details-summary {
        display: flex !important;
        gap: var(--wa-space-s);
        align-items: center;
        justify-content: space-between;
        width: 100%;
        margin-right: var(--wa-space-xs);

        wa-spinner {
            font-size: 1.2em;
        }
    }
}

.reasoning-body {
    padding: var(--wa-space-xs) 0;
    word-break: break-word;
}
</style>
