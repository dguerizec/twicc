<script setup>
// Minimal "JSON human view" of a session's workflow runs: fetch the persisted
// Workflow rows (the verbatim wf_*.json envelopes) and render each as a
// collapsible JSON tree. This validates the ingestion → endpoint → render path
// end to end; the real workflow UI comes later.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import JsonViewer from '../json/JsonViewer.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // True while the Workflows tab is the shown tab in its region.
    active: { type: Boolean, default: false },
})

const workflows = ref([])
const loading = ref(false)
const error = ref(null)
const hasLoaded = ref(false)
// Controlled collapse state shared by every run's JsonViewer; each run gets a
// distinct root path (its run_id) so paths never collide.
const collapsedPaths = ref(new Set())

let controller = null

async function load() {
    if (controller) controller.abort()
    controller = new AbortController()
    loading.value = true
    error.value = null
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}`
            + `/sessions/${encodeURIComponent(props.sessionId)}/workflows/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data = await response.json()
        workflows.value = data
        // Open compact: collapse each run at its root, expand on demand.
        collapsedPaths.value = new Set(data.map(w => w.run_id))
        hasLoaded.value = true
    } catch (e) {
        if (e.name === 'AbortError') return
        error.value = e.message || 'Failed to load workflows'
    } finally {
        loading.value = false
    }
}

function toggleCollapse(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
}

onMounted(load)
watch(() => props.active, (active) => { if (active) load() })
watch(() => props.sessionId, () => { hasLoaded.value = false; load() })
onBeforeUnmount(() => { if (controller) controller.abort() })
</script>

<template>
    <div class="workflows-pane">
        <div v-if="error" class="workflows-state workflows-error">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ error }}</span>
        </div>
        <div v-else-if="loading && !hasLoaded" class="workflows-state">Loading…</div>
        <div v-else-if="!workflows.length" class="workflows-state">No workflows for this session.</div>
        <div v-else class="workflows-list">
            <section v-for="w in workflows" :key="w.run_id" class="workflow">
                <header class="workflow-head">
                    <code class="workflow-run">{{ w.run_id }}</code>
                    <span v-if="w.raw?.workflowName" class="workflow-name">{{ w.raw.workflowName }}</span>
                    <span v-if="w.raw?.status" class="workflow-status">{{ w.raw.status }}</span>
                    <span v-if="w.raw?.agentCount != null" class="workflow-agents">{{ w.raw.agentCount }} agents</span>
                </header>
                <div class="workflow-json">
                    <JsonViewer
                        :data="w.raw"
                        :path="w.run_id"
                        :collapsed-paths="collapsedPaths"
                        @toggle="toggleCollapse"
                    />
                </div>
            </section>
        </div>
    </div>
</template>

<style scoped>
.workflows-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-m, 1rem);
}

.workflows-state {
    color: var(--wa-color-text-quiet, #8b97a7);
    padding: var(--wa-space-l, 1.5rem);
    display: flex;
    align-items: center;
    gap: var(--wa-space-s, 0.5rem);
}

.workflows-error {
    color: var(--wa-color-danger-fill-loud, #d73a49);
}

.workflows-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-l, 1.5rem);
}

.workflow {
    border: 1px solid var(--wa-color-surface-border, #2a313c);
    border-radius: var(--wa-border-radius-m, 8px);
    padding: var(--wa-space-s, 0.5rem) var(--wa-space-m, 1rem) var(--wa-space-m, 1rem);
    overflow: auto;
}

.workflow-head {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s, 0.5rem);
    flex-wrap: wrap;
    margin-bottom: var(--wa-space-xs, 0.25rem);
    font-size: var(--wa-font-size-s, 0.875rem);
}

.workflow-run {
    font-weight: 700;
    font-family: var(--wa-font-family-code, monospace);
}

.workflow-name {
    color: var(--wa-color-brand-fill-loud, #5b9aff);
    font-weight: 600;
}

.workflow-status,
.workflow-agents {
    color: var(--wa-color-text-quiet, #8b97a7);
}

.workflow-json {
    font-family: var(--wa-font-family-code, monospace);
    font-size: var(--wa-font-size-s, 0.8125rem);
    overflow: auto;
}
</style>
