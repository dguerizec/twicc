<script setup>
// Orchestration tab content: the full spawned-session tree (``spawned_by``
// links) rooted at the session's top-level ancestor, fetched from
// ``/api/projects/<pid>/sessions/<sid>/topology/`` (the same engine as
// ``twicc topology``). Intentionally not live — it renders a snapshot fetched
// on first open and refetched only when the user clicks Refresh.
import { ref, computed, watch } from 'vue'
import OrchestrationNode from './OrchestrationNode.vue'
import CostDisplay from '../ui/CostDisplay.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    active: { type: Boolean, default: false },
})

const loading = ref(false)
const error = ref(null)
const topology = ref(null)

const nodesById = computed(() => {
    const map = {}
    for (const node of topology.value?.nodes ?? []) {
        map[node.id] = node
    }
    return map
})
const rootTree = computed(() => topology.value?.tree ?? null)
const nodeCount = computed(() => topology.value?.node_count ?? 0)
// All-inclusive cost of the whole tree (the root's subtree cost).
const totalCost = computed(() => topology.value?.total_cost ?? null)

// At-a-glance activity breakdown by process state. Buckets: working
// (starting / assistant_turn), awaiting (awaiting_user_input), idle (user_turn),
// stopped (dead). Only non-empty buckets are surfaced.
const ACTIVITY_BUCKETS = [
    { label: 'working', states: ['starting', 'assistant_turn'] },
    { label: 'awaiting', states: ['awaiting_user_input'] },
    { label: 'idle', states: ['user_turn'] },
    { label: 'stopped', states: ['dead'] },
]
const activity = computed(() => {
    const counts = {}
    for (const node of topology.value?.nodes ?? []) {
        const state = node.process?.state ?? 'dead'
        counts[state] = (counts[state] ?? 0) + 1
    }
    return ACTIVITY_BUCKETS
        .map(b => ({ label: b.label, count: b.states.reduce((n, s) => n + (counts[s] ?? 0), 0) }))
        .filter(b => b.count > 0)
})

// Parenthetical shown after the session count: "All stopped" when every session
// shares one state, otherwise the per-state breakdown ("2 working · 9 stopped").
const activitySummary = computed(() => {
    const buckets = activity.value
    if (buckets.length === 0) return ''
    if (buckets.length === 1) return `All ${buckets[0].label}`
    return buckets.map(b => `${b.count} ${b.label}`).join(' · ')
})

async function load() {
    if (!props.projectId || !props.sessionId) return
    loading.value = true
    error.value = null
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}/sessions/${encodeURIComponent(props.sessionId)}/topology/`
        const response = await fetch(url)
        if (!response.ok) {
            throw new Error(`Failed to load topology: ${response.status}`)
        }
        topology.value = await response.json()
    } catch (e) {
        console.error('Failed to load orchestration topology:', e)
        error.value = 'Failed to load the orchestration topology.'
    } finally {
        loading.value = false
    }
}

// Fetch on first open; keep the snapshot across tab switches. The user
// refreshes manually to update (the tab is intentionally not live).
watch(
    () => props.active,
    (active) => {
        if (active && !topology.value && !loading.value) load()
    },
    { immediate: true }
)
</script>

<template>
    <div class="orchestration-panel">
        <div class="orch-header">
            <div class="orch-toolbar">
                <div class="orch-toolbar-meta">
                    <span class="orch-toolbar-title">Orchestration tree</span>
                    <span v-if="nodeCount" class="orch-meta-item">{{ nodeCount }} session{{ nodeCount > 1 ? 's' : '' }}<template v-if="activitySummary"> ({{ activitySummary }})</template></span>
                    <span v-if="totalCost != null" class="orch-meta-item">
                        <CostDisplay :cost="totalCost" /> total
                    </span>
                </div>
                <wa-button
                    size="small"
                    appearance="plain"
                    :loading="loading"
                    :disabled="loading"
                    @click="load"
                >
                    <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                    Refresh
                </wa-button>
            </div>
            <div class="orch-note">
                Read-only view — open a session to interact with it. Sessions marked
                <wa-icon name="eye-slash" class="orch-note-icon"></wa-icon> can't be opened.
            </div>
        </div>

        <div class="orch-content">
            <div v-if="loading && !topology" class="orch-state">
                <wa-spinner></wa-spinner>
                <span>Loading topology…</span>
            </div>
            <wa-callout v-else-if="error" variant="danger" size="small">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                {{ error }}
            </wa-callout>
            <div v-else-if="rootTree" class="orch-tree">
                <OrchestrationNode
                    :node="rootTree"
                    :nodes-by-id="nodesById"
                    :current-session-id="sessionId"
                />
            </div>
            <div v-else class="orch-state orch-state-empty">
                <wa-icon name="sitemap"></wa-icon>
                <span>No orchestration data.</span>
            </div>
        </div>
    </div>
</template>

<style scoped>
.orchestration-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.orch-header {
    flex-shrink: 0;
    padding: var(--wa-space-s) var(--wa-space-m);
    border-bottom: var(--divider-size, 1px) solid var(--wa-color-surface-border);
}

.orch-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
}

.orch-note {
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.orch-note-icon {
    /* Inline reference to the hidden-session marker, in the flow of the text. */
    vertical-align: -0.1em;
    margin-inline: 0.1em;
}

.orch-toolbar-meta {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.orch-toolbar-title {
    font-weight: 600;
}

.orch-meta-item {
    /* Plain inline flow (not inline-flex) so "$ 0.60 total" keeps the natural
       space between the amount and the label. */
    font-weight: 400;
    color: var(--wa-color-text-quiet);
}

/* Middot separator before each meta item (after the title) */
.orch-meta-item::before {
    content: '·';
    margin-right: var(--wa-space-xs);
}

.orch-content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-m);
}

/* Custom tree: all connector geometry (vertical lines + elbows) lives in
   OrchestrationNode. This is just the scroll-area root. */
.orch-tree {
    line-height: 1.5;
}

.orch-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
}

.orch-state-empty {
    flex-direction: column;
    font-size: var(--wa-font-size-l);
}
</style>
