<script setup>
// Structured body of one workflow run, rendered inside the run's wa-details.
// Works identically for a running run (STATE 1, synthetic) and a finished one
// (STATE 2, the real wf_*.json envelope); the differences are sourced per field:
//   - start time : envelope.startTime (STATE 2) or the script mtime the back
//                  injects into STATE 1 — both epoch ms.
//   - duration   : envelope.durationMs when finished; otherwise a live ticker
//                  from start time (now − start).
// Sections: (1) info, (2) description, (3) args, (4) phases, (5) result.
import { ref, computed } from 'vue'
import JsonHumanView from '../json/JsonHumanView.vue'
import MarkdownContent from '../ui/MarkdownContent.vue'
import ProcessDuration from '../ui/ProcessDuration.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { useSettingsStore } from '../../stores/settings'
import { formatDuration } from '../../utils/date'

const props = defineProps({
    // The view envelope (any of the 3 raw_json states).
    raw: { type: Object, required: true },
    // Run state: 'running' | 'completed' | 'failed' (resolved in WorkflowsPane).
    statusKind: { type: String, default: 'running' },
    // Total run cost (dedicated column, not in raw_json); null when unknown.
    cost: { type: Number, default: null },
    // Per-phase cost breakdown {phaseIndex(str): cost}; from the phases_cost column.
    phasesCost: { type: Object, default: () => ({}) },
})

const settingsStore = useSettingsStore()
const showCosts = computed(() => settingsStore.areCostsShown)

const STATE_LABELS = { running: 'Running', completed: 'Completed', failed: 'Failed' }
const stateLabel = computed(() => STATE_LABELS[props.statusKind] || 'Running')

const synthetic = computed(() => !!props.raw?.synthetic)
const agentCount = computed(() => props.raw?.agentCount ?? 0)

// Epoch ms → seconds (the date utils + ProcessDuration both take seconds).
const startTimeSec = computed(() => {
    const t = props.raw?.startTime
    return typeof t === 'number' && t > 0 ? t / 1000 : null
})
// Static duration in seconds — only for a finished run that reports durationMs.
const durationSec = computed(() => {
    if (synthetic.value) return null
    const d = props.raw?.durationMs
    return typeof d === 'number' && d >= 0 ? d / 1000 : null
})

const summary = computed(() => props.raw?.summary || '')

// --- Phases ---------------------------------------------------------------
const phases = computed(() => (Array.isArray(props.raw?.phases) ? props.raw.phases : []))
const agents = computed(() => {
    const wp = props.raw?.workflowProgress
    return Array.isArray(wp) ? wp.filter((e) => e?.type === 'workflow_agent') : []
})

// Normalize an agent's lifecycle across the two state vocabularies:
//   STATE 1 (journal): running | completed
//   STATE 2 (wf json): queued | running | done | failed | error | …
function classifyAgent(state) {
    const s = String(state || '').toLowerCase()
    if (s === 'running') return 'running' // started, not finished
    if (s === 'queued' || s === 'pending') return 'pending' // not started yet
    return 'finished' // done/completed/success/failed/error/cancelled
}

// A phase's agents are those the back/engine stamped with that phase. Match on
// phaseIndex, which is 1-based in both raw_json states (the engine's wf_*.json
// and the synthetic build_state1 mirror): the phase at array position i carries
// phaseIndex i+1 — never the 0-based array position itself.
function agentsOfPhase(index1) {
    return agents.value.filter((a) => a.phaseIndex === index1)
}

// Phase status, recomputed live (it isn't necessarily linear):
//   pending   — no agent of the phase has started
//   running   — at least one started agent isn't finished
//   completed — every started agent is finished
function phaseStatusOf(list) {
    const started = list.map((a) => classifyAgent(a.state)).filter((c) => c !== 'pending')
    if (!started.length) return 'pending'
    if (started.some((c) => c === 'running')) return 'running'
    return 'completed'
}

const phaseRows = computed(() =>
    phases.value.map((p, i) => {
        const title = p && typeof p === 'object' ? p.title : String(p)
        const detail = p && typeof p === 'object' ? p.detail || '' : ''
        const list = agentsOfPhase(i + 1)
        const cost = props.phasesCost?.[String(i + 1)]
        return {
            key: `${i}:${title}`,
            title: title || `Phase ${i + 1}`,
            detail,
            statusKind: phaseStatusOf(list),
            agentCount: list.length,
            cost: typeof cost === 'number' ? cost : null,
        }
    }),
)

// --- Result ---------------------------------------------------------------
const result = computed(() => props.raw?.result)
const hasResult = computed(() => {
    const r = result.value
    return r != null && !(typeof r === 'string' && r === '')
})
// Lazy-mount the (potentially large) JsonHumanView tree only when expanded.
const resultOpen = ref(false)
function onResultToggle(event, open) {
    if (event.target !== event.currentTarget) return // ignore nested wa-* bubbling
    resultOpen.value = open
}

// --- Args -----------------------------------------------------------------
// The launch args — only in the final wf_*.json (STATE 2). The engine stores them
// as a string: when that string is a JSON object/array, render the parsed tree in
// JsonHumanView; a plain-text arg (e.g. a research question) renders as a markdown
// block. A non-string arg (should it ever occur) goes straight to JHV. Lazy on expand.
const args = computed(() => props.raw?.args)
const hasArgs = computed(() => {
    const a = args.value
    return a != null && !(typeof a === 'string' && a === '')
})
// { mode: 'json' | 'markdown', value } — how to render the args.
const argsView = computed(() => {
    const a = args.value
    if (a == null) return null
    if (typeof a !== 'string') return { mode: 'json', value: a }
    const trimmed = a.trim()
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        try {
            return { mode: 'json', value: JSON.parse(a) }
        } catch { /* not valid JSON → fall through to markdown */ }
    }
    return { mode: 'markdown', value: a }
})
const argsOpen = ref(false)
function onArgsToggle(event, open) {
    if (event.target !== event.currentTarget) return // ignore nested wa-* bubbling
    argsOpen.value = open
}

function agentsLabel(n) {
    return `${n} ${n === 1 ? 'agent' : 'agents'}`
}
</script>

<template>
    <div class="wf-detail">
        <!-- Section 1 — info -->
        <div class="wf-info">
            <span class="wf-info-item">
                <wa-spinner v-if="statusKind === 'running'" class="wf-state-icon"></wa-spinner>
                <wa-icon v-else-if="statusKind === 'failed'" name="circle-xmark" class="wf-state-icon wf-state-failed"></wa-icon>
                <wa-icon v-else name="circle-check" class="wf-state-icon wf-state-done"></wa-icon>
                <span>{{ stateLabel }}</span>
            </span>
            <span class="wf-info-item">
                <wa-icon name="stopwatch"></wa-icon>
                <span v-if="durationSec != null">{{ formatDuration(durationSec) }}</span>
                <ProcessDuration v-else-if="synthetic && startTimeSec != null" :state-changed-at="startTimeSec" />
                <span v-else>—</span>
            </span>
            <span class="wf-info-item">
                <wa-icon name="robot"></wa-icon>
                <span>{{ agentsLabel(agentCount) }}</span>
            </span>
            <span v-if="showCosts" class="wf-info-item">
                <CostDisplay :cost="cost" />
            </span>
        </div>

        <!-- Section 2 — full description (untruncated, unlike the title) -->
        <div v-if="summary" class="wf-section">
            <div class="wf-description-label">Description</div>
            <p class="wf-description">{{ summary }}</p>
        </div>

        <!-- Section 3 — args (final wf_*.json only; string → markdown, else JSON) -->
        <div v-if="hasArgs" class="wf-section">
            <wa-details
                class="wf-row"
                icon-placement="start"
                @wa-show="onArgsToggle($event, true)"
                @wa-hide="onArgsToggle($event, false)"
            >
                <span slot="summary" class="items-details-summary">
                    <span class="items-details-summary-left">
                        <strong class="items-details-summary-name">Arguments</strong>
                    </span>
                </span>
                <div v-if="argsOpen" class="wf-row-body wf-args-body">
                    <JsonHumanView v-if="argsView?.mode === 'json'" :value="argsView.value" />
                    <MarkdownContent v-else :source="argsView?.value" />
                </div>
            </wa-details>
        </div>

        <!-- Section 4 — phases -->
        <div v-if="phaseRows.length" class="wf-section">
            <div class="wf-section-label">Phases</div>
            <wa-details
                v-for="ph in phaseRows"
                :key="ph.key"
                class="wf-row"
                icon-placement="start"
            >
                <span slot="summary" class="items-details-summary">
                    <span class="items-details-summary-left">
                        <strong class="items-details-summary-name">{{ ph.title }}</strong>
                        <template v-if="ph.detail">
                            <span class="items-details-summary-separator"> — </span>
                            <span class="items-details-summary-description">{{ ph.detail }}</span>
                        </template>
                    </span>
                    <wa-spinner v-if="ph.statusKind === 'running'" class="wf-status-icon"></wa-spinner>
                    <wa-icon v-else-if="ph.statusKind === 'pending'" name="hourglass-start" class="wf-status-icon wf-status-pending"></wa-icon>
                    <wa-icon v-else name="circle-check" class="wf-status-icon wf-status-done"></wa-icon>
                </span>
                <div class="wf-info wf-phase-info">
                    <span class="wf-info-item">
                        <wa-icon name="robot"></wa-icon>
                        <span>{{ agentsLabel(ph.agentCount) }}</span>
                    </span>
                    <span v-if="showCosts" class="wf-info-item">
                        <CostDisplay :cost="ph.cost" />
                    </span>
                </div>
            </wa-details>
        </div>

        <!-- Section 5 — result (only once there is one; full tree on expand) -->
        <div v-if="hasResult" class="wf-section">
            <wa-details
                class="wf-row"
                icon-placement="start"
                @wa-show="onResultToggle($event, true)"
                @wa-hide="onResultToggle($event, false)"
            >
                <span slot="summary" class="items-details-summary">
                    <span class="items-details-summary-left">
                        <strong class="items-details-summary-name">Result</strong>
                    </span>
                </span>
                <div v-if="resultOpen" class="wf-row-body wf-result-body">
                    <JsonHumanView :value="result" />
                </div>
            </wa-details>
        </div>
    </div>
</template>

<style scoped>
.wf-detail {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-l);
}

/* Section 1 — info */
.wf-info {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.wf-info-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

/* Run state icon in the info line — same colors as the tab/phase status. */
.wf-state-failed {
    color: var(--wa-color-danger-50);
}

.wf-state-done {
    color: var(--wa-color-success-50);
}

/* Section 2 — description */
.wf-description {
    margin: 0;
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-s);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

/* Sections 3 & 4 */
.wf-section {
    display: flex;
    flex-direction: column;
}

.wf-section-label,
.wf-description-label {
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    margin-bottom: var(--wa-space-xs);
}

/* Phase / result rows mirror the chat's tool-row summary, like the run header in
 * WorkflowsPane: caret left, "Name — description" left, status icon pinned right.
 * The inner text classes (.items-details-summary-description, …) are declared
 * globally by ToolUseContent.vue; the layout classes are scoped there, so we
 * replicate the bits we need. */
.wf-row {
    font-size: var(--wa-font-size-s);
}

.wf-row::part(header) {
    padding-right: 6px;
}

.wf-row .items-details-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-m);
    width: 100%;
}

.wf-row .items-details-summary-left {
    flex: 1;
    min-width: 60%;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    max-width: 100%;
}

.wf-row .items-details-summary-name {
    color: var(--wa-color-text-normal);
    font-weight: 600;
}

.wf-row .items-details-summary-separator {
    color: var(--wa-color-text-quiet);
}

.wf-row .wf-status-icon {
    font-size: 1.2em;
    margin-left: auto;
}

.wf-row .wf-status-done {
    color: var(--wa-color-success-50);
}

.wf-row .wf-status-pending {
    color: var(--wa-color-text-quiet);
}

.wf-row-body {
    color: var(--wa-color-text-quiet);
}

.wf-result-body,
.wf-args-body {
    overflow-x: auto;
}
</style>
