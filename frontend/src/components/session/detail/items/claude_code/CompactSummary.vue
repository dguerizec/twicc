<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const dataStore = useDataStore()

const props = defineProps({
    content: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    detailKey: {
        type: String,
        required: true
    }
})

const detailsRef = ref(null)

// Lazy rendering: content is only mounted when wa-details is open.
// Initialized from the store to restore state across virtual scroller mount/unmount cycles.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, props.detailKey))

// Skip open animation when mounting already-open (virtual scroller restoration).
// Same pattern as ThinkingContent.
const instantOpen = ref(isOpen.value)

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onShow() {
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, props.detailKey, true)
}

function onHide() {
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, props.detailKey, false)
}
</script>

<template>
    <wa-details ref="detailsRef" :open="isOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="item-details compact-summary-content" icon-placement="start" @wa-show="onShow" @wa-hide="onHide">
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Session compacted</strong>
        </span>
        <div v-if="isOpen" class="compact-summary-body">
            <MarkdownContent :source="content" />
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

.compact-summary-body {
    padding: var(--wa-space-xs) 0;
    word-break: break-word;
}
</style>
