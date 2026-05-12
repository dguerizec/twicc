<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    // Parsed Codex JSONL line:
    //   { type: 'response_item', payload: { type: 'reasoning', summary: [...], ... } }
    data: {
        type: Object,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    // Used to build the persisted open/closed key for this collapsible.
    lineNum: {
        type: Number,
        required: true,
    },
})

const dataStore = useDataStore()

// Reasoning items are mono-block (one summary list per JSONL line), so a
// ``:0`` suffix matches the convention the streaming code uses for
// block indices.
const detailKey = computed(() => `line:${props.lineNum}:0`)

// Concatenate every ``summary_text`` block into a single markdown source.
// Codex sometimes emits more than one summary entry per reasoning step;
// joining with ``\n\n`` keeps them as separate paragraphs / headings.
const text = computed(() => {
    const summary = props.data?.payload?.summary
    if (!Array.isArray(summary)) return ''
    return summary
        .filter(s => s?.type === 'summary_text' && typeof s.text === 'string' && s.text.trim())
        .map(s => s.text)
        .join('\n\n')
})

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
    }
}

.reasoning-body {
    padding: var(--wa-space-xs) 0;
    word-break: break-word;
}
</style>
