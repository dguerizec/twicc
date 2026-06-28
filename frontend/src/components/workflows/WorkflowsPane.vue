<script setup>
// A session's workflow runs: fetch the persisted Workflow rows (the 3-state
// view envelope) and render each in a wa-details. The summary header mirrors the
// chat's tool rows — caret left, "Name — description", a status icon (spinner
// while running, check/xmark when done) pinned right. The body is rendered lazily
// (v-if on open) and is still the raw JsonHumanView tree; the structured running
// view comes later.
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import WorkflowRunDetail from './WorkflowRunDetail.vue'
import { generateTemplates, sha256Hex, extractMeta } from '../../utils/workflowTemplates'

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // run_id to focus (open + scroll), from a "View Workflow" navigation.
    focusRunId: { type: String, default: null },
    // True while the Workflows tab is the shown tab in its region.
    active: { type: Boolean, default: false },
})

const paneRef = ref(null)
const workflows = ref([])
const loading = ref(false)
const error = ref(null)
const hasLoaded = ref(false)
// run_ids whose body is open (and thus mounted).
const openRuns = ref(new Set())

let controller = null
// `${run_id}:${scriptHash}` already generated + POSTed this component's life,
// so a STATE 0 run is synthesized at most once per script version (dedupe across
// the many load() triggers: mount, tab activation, live workflow_changed).
const synthesized = new Set()

// "find-flaky-tests" → "Find Flaky Tests": dashes to spaces, capitalize each word.
function titleizeName(name) {
    if (!name) return ''
    return String(name)
        .split(/[-\s]+/)
        .filter(Boolean)
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ')
}

// Status shown by the summary's right-side icon:
//   running   → spinner (STATE 0/1 synthetic, or a real envelope still mid-run)
//   completed → check     terminal success
//   failed    → xmark      terminal failure
// STATE 2's status is whatever the wf_*.json reports; STATE 0/1 are always running.
function statusKindOf(raw) {
    if (raw.synthetic) return 'running'
    const s = String(raw.status || '').toLowerCase()
    if (s === 'completed' || s === 'success' || s === 'done') return 'completed'
    if (s === 'failed' || s === 'error' || s === 'cancelled' || s === 'canceled') return 'failed'
    return 'running'
}

// Decorate each run with the fields the summary header needs. Name/description
// come from the envelope (workflowName/summary); for a STATE 0 row not yet
// synthesized those are absent, so we read them straight from the launch script's
// meta literal — a correct first paint before STATE 1 lands.
const rows = computed(() => workflows.value.map((w) => {
    const raw = w.raw || {}
    let name = raw.workflowName
    let summary = raw.summary
    if ((name == null || summary == null) && typeof raw.script === 'string' && raw.script) {
        const meta = extractMeta(raw.script)
        if (name == null) name = meta.name
        if (summary == null) summary = meta.description
    }
    return {
        run_id: w.run_id,
        raw,
        name: titleizeName(name),
        summary: summary || '',
        statusKind: statusKindOf(raw),
    }
}))

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
        // Preserve open runs across live refetches; auto-open the newest only
        // when nothing is open; focus the targeted run when it's present.
        const ids = new Set(data.map(w => w.run_id))
        openRuns.value = new Set([...openRuns.value].filter(id => ids.has(id)))
        if (!openRuns.value.size && data.length) openRuns.value.add(data[0].run_id)
        hasLoaded.value = true
        maybeSynthesize(data)
        if (props.focusRunId && ids.has(props.focusRunId)) {
            openRuns.value.add(props.focusRunId)
            scrollToRun(props.focusRunId)
        }
    } catch (e) {
        if (e.name === 'AbortError') return
        error.value = e.message || 'Failed to load workflows'
    } finally {
        loading.value = false
    }
}

// A RUNNING run can't be phase-tagged on the back alone: building the templates
// the detector needs means *executing* the workflow script. When a run is STATE
// 0 (synthetic, no phases yet), generate {meta, templates} in the browser from
// its launch script and POST them — the back then builds the running view
// (STATE 1) and broadcasts, which refetches here. No-op for STATE 1/2.
async function maybeSynthesize(list) {
    for (const w of list) {
        const raw = w.raw
        if (!raw || !raw.synthetic) continue                          // STATE 2 (real) — done
        if (Array.isArray(raw.phases) && raw.phases.length) continue  // STATE 1 — already synthesized
        if (typeof raw.script !== 'string' || !raw.script) continue
        let hash
        try { hash = await sha256Hex(raw.script) } catch { continue }
        const key = `${w.run_id}:${hash}`
        if (synthesized.has(key)) continue
        synthesized.add(key)
        try {
            const { meta, templates } = await generateTemplates(raw.script, { runs: 100 })
            await postSynthesis(w.run_id, meta, templates, hash, key)
        } catch (e) {
            // Generation is deterministic — a retry on the same script won't help;
            // keep the guard and surface it for debugging.
            console.warn('[workflow] template generation failed for', w.run_id, e)
        }
    }
}

async function postSynthesis(runId, meta, templates, scriptHash, key) {
    const url = `/api/projects/${encodeURIComponent(props.projectId)}`
        + `/sessions/${encodeURIComponent(props.sessionId)}`
        + `/workflows/${encodeURIComponent(runId)}/synthesis/`
    let res
    try {
        res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ meta, templates, script_hash: scriptHash }),
        })
    } catch {
        synthesized.delete(key)   // transient network error — let a later load() retry
        return
    }
    // 5xx → transient, allow retry. 4xx (stale hash / completed / bad input):
    // a changed script re-triggers under a new hash, a done run needs nothing —
    // keep the guard. On success the back broadcasts workflow_changed, which
    // refetches and renders STATE 1; no explicit reload needed here.
    if (!res.ok && res.status >= 500) synthesized.delete(key)
}

// Keep openRuns in sync with native wa-details toggles. Guard against custom
// events bubbling up from any nested wa-* element (only react to our own).
function onRunToggle(event, runId, open) {
    if (event.target !== event.currentTarget) return
    if (open) openRuns.value.add(runId)
    else openRuns.value.delete(runId)
}

function scrollToRun(runId) {
    nextTick(() => {
        const el = paneRef.value?.querySelector(`section[data-run-id="${CSS.escape(runId)}"]`)
        el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
}

// Open + scroll the targeted run (from a "View Workflow" click). If it isn't in
// the loaded list yet — a brand-new run whose row just appeared — refetch; the
// reload opens it once present (the live workflow_changed event also refetches).
function focusRun(runId) {
    if (!runId) return
    openRuns.value.add(runId)
    if (hasLoaded.value && !workflows.value.some(w => w.run_id === runId)) {
        load()
        return
    }
    scrollToRun(runId)
}

// Live refresh: the watcher broadcasts workflow_changed when a wf_*.json is
// created/updated; debounce bursts (the engine rewrites the file each tick).
let reloadTimer = null
function onWorkflowChanged(event) {
    if (event.detail?.sessionId !== props.sessionId) return
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => { if (hasLoaded.value) load() }, 400)
}

onMounted(() => {
    load()
    window.addEventListener('twicc:workflow-changed', onWorkflowChanged)
})
watch(() => props.active, (active) => { if (active) load() })
watch(() => props.sessionId, () => { hasLoaded.value = false; synthesized.clear(); load() })
watch(() => props.focusRunId, (runId) => { if (runId && hasLoaded.value) focusRun(runId) })
onBeforeUnmount(() => {
    window.removeEventListener('twicc:workflow-changed', onWorkflowChanged)
    clearTimeout(reloadTimer)
    if (controller) controller.abort()
})
</script>

<template>
    <div class="workflows-pane" ref="paneRef">
        <div v-if="error" class="workflows-state workflows-error">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ error }}</span>
        </div>
        <div v-else-if="loading && !hasLoaded" class="workflows-state">Loading…</div>
        <div v-else-if="!workflows.length" class="workflows-state">No workflows for this session.</div>
        <div v-else class="workflows-list">
            <section v-for="row in rows" :key="row.run_id" class="workflow" :data-run-id="row.run_id">
                <wa-details
                    class="workflow-details"
                    icon-placement="start"
                    :open="openRuns.has(row.run_id)"
                    @wa-show="onRunToggle($event, row.run_id, true)"
                    @wa-hide="onRunToggle($event, row.run_id, false)"
                >
                    <span slot="summary" class="items-details-summary">
                        <span class="items-details-summary-left">
                            <strong class="items-details-summary-name">{{ row.name }}</strong>
                            <template v-if="row.summary">
                                <span class="items-details-summary-separator"> — </span>
                                <span class="items-details-summary-description">{{ row.summary }}</span>
                            </template>
                        </span>
                        <wa-spinner v-if="row.statusKind === 'running'" class="workflow-status-icon workflow-status-running"></wa-spinner>
                        <wa-icon v-else-if="row.statusKind === 'failed'" name="circle-xmark" class="workflow-status-icon workflow-status-failed"></wa-icon>
                        <wa-icon v-else name="circle-check" class="workflow-status-icon workflow-status-done"></wa-icon>
                    </span>
                    <div v-if="openRuns.has(row.run_id)" class="workflow-body">
                        <WorkflowRunDetail :raw="row.raw" />
                    </div>
                </wa-details>
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
    gap: var(--wa-space-m, 1rem);
}

/* Mirror the chat's tool-row summary (ToolUseContent.vue + SessionItem.vue):
 * caret on the left (icon-placement="start"), name — description on the left,
 * status icon pinned right. The .items-details-summary* layout classes are
 * scoped per-component there, so we replicate the bits we need here; the inner
 * text classes (.items-details-summary-description, …) are declared globally by
 * ToolUseContent.vue and apply as-is. */
.workflow-details {
    font-size: var(--wa-font-size-s);
}

.workflow-details::part(header) {
    padding-right: 6px;
}

.workflow-details .items-details-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-m);
    width: 100%;
}

.workflow-details .items-details-summary-left {
    flex: 1;
    min-width: 60%; /* force the status icon to wrap below before text gets too narrow */
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    max-width: 100%;
}

.workflow-details .items-details-summary-name {
    color: var(--wa-color-text-normal);
    font-weight: 600;
}

.workflow-details .items-details-summary-separator {
    color: var(--wa-color-text-quiet);
}

.workflow-details .workflow-status-icon {
    font-size: 1.2em;
    margin-left: auto; /* stay right-aligned even when the row wraps */
}

.workflow-details .workflow-status-done {
    color: var(--wa-color-success-50);
}

.workflow-details .workflow-status-failed {
    color: var(--wa-color-danger-50);
}

.workflow-body {
    overflow: auto;
}
</style>
