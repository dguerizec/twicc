<script setup>
// A session's workflow runs: fetch the persisted Workflow rows (the 3-state view
// envelope) and render them as a tab bar — one tab per run, labelled with the
// title-cased workflow name + a status icon (spinner running, check/xmark done).
// The active tab's panel holds the run's structured detail (WorkflowRunDetail).
// Newest run active by default; a "View Workflow" navigation selects its tab.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import WorkflowRunDetail from './WorkflowRunDetail.vue'
import { generateTemplates, sha256Hex, extractMeta } from '../../utils/workflowTemplates'

const router = useRouter()
const route = useRoute()

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // run_id to focus (select its tab), from a "View Workflow" navigation.
    focusRunId: { type: String, default: null },
    // True while the Workflows tab is the shown tab in its region.
    active: { type: Boolean, default: false },
})

const workflows = ref([])
const loading = ref(false)
const error = ref(null)
const hasLoaded = ref(false)
// run_id of the active tab (controlled wa-tab-group). Null before the first load.
const activeRunId = ref(null)

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

// Status shown by the tab's icon:
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

// Decorate each run with the fields the tab + panel need. Name comes from the
// envelope (workflowName); for a STATE 0 row not yet synthesized it's absent, so
// we read it straight from the launch script's meta literal — a correct first
// paint before STATE 1 lands.
const rows = computed(() => workflows.value.map((w) => {
    const raw = w.raw || {}
    let name = raw.workflowName
    if (name == null && typeof raw.script === 'string' && raw.script) {
        name = extractMeta(raw.script).name
    }
    return {
        run_id: w.run_id,
        raw,
        name: titleizeName(name),
        statusKind: statusKindOf(raw),
        // Cost lives in dedicated columns (not raw_json): total + {phaseIndex: cost}.
        cost: typeof w.cost === 'number' ? w.cost : null,
        phasesCost: w.phases_cost || {},
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
        // Keep the active tab across live refetches when it's still present;
        // otherwise default to the newest run (data is newest-first). A pending
        // "View Workflow" navigation wins.
        const ids = new Set(data.map(w => w.run_id))
        if (!activeRunId.value || !ids.has(activeRunId.value)) {
            activeRunId.value = data.length ? data[0].run_id : null
        }
        hasLoaded.value = true
        maybeSynthesize(data)
        if (props.focusRunId && ids.has(props.focusRunId)) {
            activeRunId.value = props.focusRunId
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

// User switched tabs. wa-tab-group also (re)emits wa-tab-show on (re)mount for
// the already-active tab — forward only real changes (re-affirmation guard, which
// also keeps programmatic :active changes from re-writing the URL). The template
// uses @wa-tab-show.stop so this never reaches DockRegion's own wa-tab-group (our
// pane lives inside one of its tabs).
function onTabShow(event) {
    const name = event.detail?.name
    if (!name || name === activeRunId.value) return
    activeRunId.value = name
    syncUrl(name)
}

// URL-drive the tabs: reflect the active run in the address bar so a tab click is
// a real navigation (replace → no history spam). The route's runId param flows
// back as focusRunId, keeping clicks and external "View Workflow" links in sync.
// No-op when the URL already targets this run.
function syncUrl(runId) {
    if (route.params.runId === runId) return
    const name = route.name?.startsWith('projects-') ? 'projects-session-workflows' : 'session-workflows'
    router.replace({
        name,
        params: { projectId: props.projectId, sessionId: props.sessionId, runId },
    }).catch(() => {})
}

// Select the targeted run's tab (from a "View Workflow" click). If it isn't in
// the loaded list yet — a brand-new run whose row just appeared — refetch; load()
// activates it once present (the live workflow_changed event also refetches).
function focusRun(runId) {
    if (!runId) return
    if (hasLoaded.value && !workflows.value.some(w => w.run_id === runId)) {
        load()
        return
    }
    activeRunId.value = runId
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
    <div class="workflows-pane">
        <div v-if="error" class="workflows-state workflows-error">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ error }}</span>
        </div>
        <div v-else-if="loading && !hasLoaded" class="workflows-state">Loading…</div>
        <div v-else-if="!rows.length" class="workflows-state">No workflows for this session.</div>
        <wa-tab-group v-else class="workflows-tabs" :active="activeRunId" @wa-tab-show.stop="onTabShow">
            <wa-tab v-for="row in rows" :key="row.run_id" slot="nav" :panel="row.run_id" class="workflow-tab">
                <span class="workflow-tab-name">{{ row.name }}</span>
                <wa-spinner v-if="row.statusKind === 'running'" class="workflow-tab-status"></wa-spinner>
                <wa-icon v-else-if="row.statusKind === 'failed'" name="circle-xmark" class="workflow-tab-status workflow-tab-status-failed"></wa-icon>
                <wa-icon v-else name="circle-check" class="workflow-tab-status workflow-tab-status-done"></wa-icon>
            </wa-tab>
            <wa-tab-panel v-for="row in rows" :key="row.run_id" :name="row.run_id" class="workflow-panel">
                <WorkflowRunDetail :raw="row.raw" :status-kind="row.statusKind" :cost="row.cost" :phases-cost="row.phasesCost" :project-id="projectId" :session-id="sessionId" />
            </wa-tab-panel>
        </wa-tab-group>
    </div>
</template>

<style scoped>
.workflows-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
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

/* One tab per run; the active panel holds its structured detail. Make the
 * tab-group fill the pane and scroll its body (not the whole pane), so the tab
 * bar stays put while a long run detail scrolls. */
.workflows-tabs {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.workflows-tabs::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.workflows-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: auto;
}

/* Compact tab bar (the default wa-tab padding is tall) — same recipe as the
 * terminal tab bar: smaller label font, trimmed tab padding, no nav padding.
 * Scoped to the tabs so the panel content keeps its own sizing. */
.workflows-tabs::part(nav) {
    padding-bottom: 0;
}

.workflow-tab {
    font-size: var(--wa-font-size-s);
}

/* Tab label: title-cased name + a status icon right after it. */
.workflow-tab::part(base) {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    gap: var(--wa-space-2xs);
}

.workflow-tab-status {
    font-size: 1.1em;
}

.workflow-tab-status-done {
    color: var(--wa-color-success-50);
}

.workflow-tab-status-failed {
    color: var(--wa-color-danger-50);
}

.workflow-panel {
    --padding: var(--wa-space-m);
}
</style>
