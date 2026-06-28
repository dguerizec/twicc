<script setup>
// Structured body of one workflow run, rendered inside the run's wa-details.
// Works identically for a running run (STATE 1, synthetic) and a finished one
// (STATE 2, the real wf_*.json envelope); the differences are sourced per field:
//   - start time : envelope.startTime (STATE 2) or the script mtime the back
//                  injects into STATE 1 — both epoch ms.
//   - duration   : envelope.durationMs when finished; otherwise a live ticker
//                  from start time (now − start).
// Sections: (1) info, (2) full description, (3) phases, (4) result.
import { ref, computed } from 'vue'
import JsonHumanView from '../json/JsonHumanView.vue'
import ProcessDuration from '../ui/ProcessDuration.vue'
import { formatDate, formatDuration } from '../../utils/date'

const props = defineProps({
    // The view envelope (any of the 3 raw_json states).
    raw: { type: Object, required: true },
})

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
        return {
            key: `${i}:${title}`,
            title: title || `Phase ${i + 1}`,
            detail,
            statusKind: phaseStatusOf(list),
            agentCount: list.length,
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

function agentsLabel(n) {
    return `${n} ${n === 1 ? 'agent' : 'agents'}`
}
</script>

<template>
    <div class="wf-detail">
        <!-- Section 1 — info -->
        <div class="wf-info">
            <span class="wf-info-item">
                <wa-icon name="clock"></wa-icon>
                <span>{{ startTimeSec != null ? formatDate(startTimeSec, { smart: true }) : '—' }}</span>
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
        </div>

        <!-- Section 2 — full description (untruncated, unlike the title) -->
        <div v-if="summary" class="wf-section">
            <div class="wf-description-label">Description</div>
            <p class="wf-description">{{ summary }}</p>
        </div>

        <!-- Section 3 — phases -->
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
                <div class="wf-row-body">{{ agentsLabel(ph.agentCount) }}</div>
            </wa-details>
        </div>

        <!-- Section 4 — result (only once there is one; full tree on expand) -->
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
    gap: var(--wa-space-l);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.wf-info-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
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

.wf-result-body {
    overflow-x: auto;
}
</style>
