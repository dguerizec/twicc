<script setup>
import { computed, ref, watch, onMounted, provide, inject } from 'vue'
import VirtualScroller from '../components/virtual-scroller/VirtualScroller.vue'
import SessionItem from '../components/session/detail/SessionItem.vue'
import GroupToggle from '../components/session/detail/GroupToggle.vue'
import DaySeparator from '../components/session/detail/items/DaySeparator.vue'
import { useDataStore } from '../stores/data'          // aliased → dataStoreShim
import { useSettingsStore } from '../stores/settings'  // aliased → settingsStoreShim
import { getParsedContent, hasContent } from '../utils/parsedContent'
import { useDebounceFn } from '@vueuse/core'

const props = defineProps({
    projectId: { type: String, default: 'share' },
    sessionId: { type: String, required: true },
    parentSessionId: { type: String, default: null },
    lastLine: { type: Number, required: true },
})

const store = useDataStore()
const settings = useSettingsStore()
const scrollerRef = ref(null)
const INITIAL = 100, BUFFER = 40, MIN_ITEM = 40

const visualItems = computed(() => store.getSessionVisualItems(props.sessionId))

async function loadInitial() {
    const ranges = []
    if (props.lastLine <= INITIAL) ranges.push([1, props.lastLine])
    else if (props.parentSessionId) ranges.push([1, INITIAL])
    else ranges.push([props.lastLine - INITIAL + 1, props.lastLine])
    const qs = new URLSearchParams()
    for (const [lo, hi] of ranges) qs.append('range', `${lo}:${hi}`)
    const [metadata] = await Promise.all([
        store.loadSessionMetadata(props.projectId, props.sessionId, props.parentSessionId),
        store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId),
        // Completion state of every visible tool call — without it every tool
        // renders as running (resultCount 0). Live updates then flow via WS.
        store.fetchToolStates(props.projectId, props.sessionId, props.parentSessionId),
    ])
    if (metadata) {
        // Metadata first initializes the array; the ranges call above already
        // added content for the initial window — re-apply metadata then let the
        // content fill (order-independent because both recompute).
        store.initSessionItemsFromMetadata(props.sessionId, metadata)
        await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
    }
}
onMounted(loadInitial)

const pending = ref(null)
const flush = useDebounceFn(async () => {
    const lines = pending.value; pending.value = null
    if (!lines?.length) return
    // Coalesce contiguous line numbers into ranges.
    const sorted = [...new Set(lines)].sort((a, b) => a - b)
    const ranges = []; let s = sorted[0], e = sorted[0]
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === e + 1) e = sorted[i]
        else { ranges.push([s, e]); s = e = sorted[i] }
    }
    ranges.push([s, e])
    await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
}, 120)

function onUpdate({ visibleStartIndex, visibleEndIndex }) {
    const vis = visualItems.value
    if (!vis?.length) return
    const lo = Math.max(0, visibleStartIndex - BUFFER)
    const hi = Math.min(vis.length - 1, visibleEndIndex + BUFFER)
    const need = []
    for (let i = lo; i <= hi; i++) {
        const vi = vis[i]
        if (vi && !vi.isDaySeparator && !hasContent(vi)) need.push(vi.lineNum)
    }
    if (need.length) { pending.value = need; flush() }
}

function toggleGroup(head) { store.toggleExpandedGroup(props.sessionId, head) }

// The reused components inject these; provide the media rewrite + the tool-result
// fetch seam (both share-mode). parentSessionId routes subagent tool-results.
const shareApi = inject('shareApi')
provide('fetchToolResult', (lineNum, toolId, parentSessionId) =>
    shareApi.fetchToolResults(lineNum, toolId, parentSessionId || null))
// Bound to THIS list's session context (root vs subagent) so the reused Edit /
// apply_patch diff can pull its ceiling-filtered tool_result line by tool id.
provide('fetchBackendPatchItems', (toolId) =>
    shareApi.fetchBackendPatchItems(toolId, props.parentSessionId || null))
provide('rewriteContentMediaUrl', (url) => {
    // /artifacts/<sid>/<file> → /share/<t>/media/<file> when sid === shared session.
    const m = /^\/artifacts\/([^/]+)\/([^/?#]+)$/.exec(url)
    if (m && m[1] === props.sessionId) return shareApi.mediaUrl(m[2])
    if (m) return null       // a different session's artifact — not shared
    return url
})

// The reused settings store has a recompute watcher; the shim doesn't, so rebuild
// the visual items when the viewer changes the display mode or timestamp toggle.
watch(() => [settings.displayMode, settings.areMessageTimestampsShown],
    () => store.recomputeVisualItems(props.sessionId))
</script>

<template>
    <div class="session-items-list share-items-list">
        <VirtualScroller
            ref="scrollerRef"
            :items="visualItems"
            :item-key="(item) => item.lineNum"
            :min-item-height="MIN_ITEM"
            :buffer="5000"
            :unload-buffer="10000"
            :prevent-auto-scroll-to-bottom="!!parentSessionId"
            class="session-items"
            @update="onUpdate"
        >
            <template #default="{ item }">
                <DaySeparator v-if="item.isDaySeparator" :label="item.dayLabel" :day-key="item.dayKey" />
                <div v-else-if="!hasContent(item)"
                     :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                     :style="{ minHeight: MIN_ITEM + 'px' }"></div>
                <template v-else-if="item.isGroupHead">
                    <GroupToggle
                        :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd && !item.isExpanded }"
                        :expanded="item.isExpanded" :item-count="item.groupSize" :comments-count="0"
                        @toggle="toggleGroup(item.lineNum)" />
                    <SessionItem v-if="item.isExpanded" :class="{ 'is-block-end': item.isBlockEnd }"
                        :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                        :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                        :line-num="item.lineNum" :externally-grouped="item.externallyGrouped || false"
                        :is-block-end="item.isBlockEnd || false" />
                </template>
                <SessionItem v-else
                    :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                    :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                    :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                    :line-num="item.lineNum" :externally-grouped="item.externallyGrouped || false"
                    :group-head="item.groupHead" :group-tail="item.groupTail"
                    :prefix-expanded="item.prefixExpanded || false" :suffix-expanded="item.suffixExpanded || false"
                    :detail-toggle-for="item.detailToggleFor ?? null" :is-block-end="item.isBlockEnd || false"
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)" />
            </template>
        </VirtualScroller>
    </div>
</template>
