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
import { useRouter, useRoute } from 'vue-router'
import JsonHumanView from '../json/JsonHumanView.vue'
import MarkdownContent from '../ui/MarkdownContent.vue'
import ProcessDuration from '../ui/ProcessDuration.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import WorkflowStateBadge from './WorkflowStateBadge.vue'
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
    // The run's project + main session — for the "View Agent" subagent route.
    projectId: { type: String, default: null },
    sessionId: { type: String, default: null },
})

const router = useRouter()
const route = useRoute()
const settingsStore = useSettingsStore()
const showCosts = computed(() => settingsStore.areCostsShown)

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

// Phase status now comes from the back: each workflow_phase entry carries a
// derived `state` (pending/running/completed), stamped identically for STATE 1
// and STATE 2 (stamp_phase_states). Map it by the phase's 1-based index; the
// front falls back to phaseStatusOf when absent (old envelopes, or an unstamped
// phase) and still computes the Unassigned bucket's status itself.
const phaseStateByIndex = computed(() => {
    const wp = props.raw?.workflowProgress
    const map = {}
    if (Array.isArray(wp)) {
        for (const e of wp) {
            if (e?.type === 'workflow_phase' && e.index != null && e.state) map[e.index] = e.state
        }
    }
    return map
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

// Agent state for the WorkflowStateBadge (distinguishes completed from failed,
// which classifyAgent lumps into 'finished').
function agentStatusKind(state) {
    const s = String(state || '').toLowerCase()
    if (s === 'running') return 'running'
    if (s === 'queued' || s === 'pending') return 'pending'
    if (s === 'failed' || s === 'error' || s === 'cancelled' || s === 'canceled') return 'failed'
    return 'completed' // done/completed/success
}

// A phase's agents are those the back/engine stamped with that phase. Match on
// phaseIndex, which is 1-based in both raw_json states (the engine's wf_*.json
// and the synthetic build_state1 mirror): the phase at array position i carries
// phaseIndex i+1 — never the 0-based array position itself.
function agentsOfPhase(index1) {
    return agents.value.filter((a) => a.phaseIndex === index1)
}

// Phase status from a list of agents (it isn't necessarily linear). Now a
// fallback to the back's stamped phase.state (see phaseStateByIndex), still the
// primary source for the Unassigned bucket, which has no workflow_phase entry:
//   pending   — no agent of the phase has started
//   running   — at least one started agent isn't finished
//   completed — every started agent is finished
function phaseStatusOf(list) {
    const started = list.map((a) => classifyAgent(a.state)).filter((c) => c !== 'pending')
    if (!started.length) return 'pending'
    if (started.some((c) => c === 'running')) return 'running'
    return 'completed'
}

// Map raw workflow_agent entries to the shape the agent rows render. Shared by
// the phase rows and the unassigned bucket.
function mapAgents(list) {
    return list.map((a) => ({
        agentId: a.agentId,
        name: a.label || a.agentId, // label when the engine gives one, else the id
        statusKind: agentStatusKind(a.state),
        active: classifyAgent(a.state) !== 'finished', // still running/queued → pulsing robot
        durationMs: typeof a.durationMs === 'number' ? a.durationMs : null,
        cost: typeof a.cost === 'number' ? a.cost : null,
        promptPreview: a.promptPreview || null,
        resultView: a.resultPreview != null ? jsonOrMarkdown(a.resultPreview) : null,
    }))
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
            // Prefer the back's stamped phase.state; fall back to local derivation.
            statusKind: phaseStateByIndex.value[i + 1] || phaseStatusOf(list),
            agentCount: list.length,
            cost: typeof cost === 'number' ? cost : null,
            agents: mapAgents(list),
        }
    }),
)

// Agents the back/engine left without a phase — STATE 1 transient (prompt not
// synced yet, so no detection) or a permanent detection miss (prompt present
// but no template matched). Without this bucket they'd belong to no phase row
// yet still count in the header's agentCount — silently vanishing. Robust
// definition: any agent whose phaseIndex matches no declared phase. Rendered
// like a phase, last. Empty (e.g. every STATE 2 agent has a phase) → omitted.
const unassignedRow = computed(() => {
    const declared = new Set(phases.value.map((_, i) => i + 1))
    const list = agents.value.filter((a) => !declared.has(a.phaseIndex))
    if (!list.length) return null
    const costs = list.map((a) => a.cost).filter((c) => typeof c === 'number')
    return {
        key: '__unassigned__',
        title: 'Unassigned',
        detail: 'Agents not matched to a phase',
        statusKind: phaseStatusOf(list),
        agentCount: list.length,
        cost: costs.length ? costs.reduce((sum, c) => sum + c, 0) : null,
        agents: mapAgents(list),
    }
})

// Phases first, then the unassigned bucket (when non-empty) — so every agent in
// the header count is visible somewhere.
const displayRows = computed(() =>
    unassignedRow.value ? [...phaseRows.value, unassignedRow.value] : phaseRows.value,
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

// Decide how to render a value: a JSON object/array — or a string that parses to
// one — goes to JsonHumanView; anything else (plain text, a truncated preview)
// renders as a markdown block. Shared by args and agent result previews.
function jsonOrMarkdown(value) {
    if (value == null) return null
    if (typeof value !== 'string') return { mode: 'json', value }
    const trimmed = value.trim()
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        try {
            return { mode: 'json', value: JSON.parse(value) }
        } catch { /* not valid JSON → fall through to markdown */ }
    }
    return { mode: 'markdown', value }
}

// --- Args -----------------------------------------------------------------
// The launch args — from the final wf_*.json (STATE 2) or recovered from the
// launching tool_use for a live run. JSON → JsonHumanView, plain text (e.g. a
// research question) → markdown. Lazy on expand.
const args = computed(() => props.raw?.args)
const hasArgs = computed(() => {
    const a = args.value
    return a != null && !(typeof a === 'string' && a === '')
})
const argsView = computed(() => jsonOrMarkdown(args.value))
const argsOpen = ref(false)
function onArgsToggle(event, open) {
    if (event.target !== event.currentTarget) return // ignore nested wa-* bubbling
    argsOpen.value = open
}

// --- Agents (within a phase) ----------------------------------------------
// Every disclosure body (phase, agent, and each agent's prompt/result) is
// lazy-mounted — tracked in one Set of prefixed open keys (phase:/agent:/
// prompt:/result:), so a 100-agent run renders nothing heavy until expanded.
const open = ref(new Set())
function toggleOpen(event, key, isOpen) {
    if (event.target !== event.currentTarget) return // ignore nested wa-* bubbling
    if (isOpen) open.value.add(key)
    else open.value.delete(key)
}

// "View Agent" opens the workflow subagent in its own tab — the same route the
// chat uses. A workflow agent's Session id is the composite <run_id>:<agent_id>
// and its parent is this run's main session, so the subagent route resolves it.
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
function viewAgent(agentId) {
    const runId = props.raw?.runId
    if (!runId || !props.sessionId) return
    router.push({
        name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
        params: {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentId: `${runId}:${agentId}`,
        },
    }).catch(() => {})
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
                <WorkflowStateBadge :kind="statusKind" />
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

        <!-- Section 4 — phases (+ the unassigned bucket, last, when non-empty) -->
        <div v-if="displayRows.length" class="wf-section">
            <div class="wf-section-label">Phases</div>
            <wa-details
                v-for="ph in displayRows"
                :key="ph.key"
                class="wf-row"
                icon-placement="start"
                :open="open.has('phase:' + ph.key)"
                @wa-show="toggleOpen($event, 'phase:' + ph.key, true)"
                @wa-hide="toggleOpen($event, 'phase:' + ph.key, false)"
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
                <div v-if="open.has('phase:' + ph.key)" class="wf-phase-body">
                    <div class="wf-info wf-phase-info">
                        <span class="wf-info-item">
                            <WorkflowStateBadge :kind="ph.statusKind" />
                        </span>
                        <span class="wf-info-item">
                            <wa-icon name="robot"></wa-icon>
                            <span>{{ agentsLabel(ph.agentCount) }}</span>
                        </span>
                        <span v-if="showCosts" class="wf-info-item">
                            <CostDisplay :cost="ph.cost" />
                        </span>
                    </div>
                    <div v-if="ph.agents.length" class="wf-agents">
                        <wa-details
                            v-for="ag in ph.agents"
                            :key="ag.agentId"
                            class="wf-row wf-agent"
                            icon-placement="start"
                            :open="open.has('agent:' + ag.agentId)"
                            @wa-show="toggleOpen($event, 'agent:' + ag.agentId, true)"
                            @wa-hide="toggleOpen($event, 'agent:' + ag.agentId, false)"
                        >
                            <span slot="summary" class="items-details-summary">
                                <span class="items-details-summary-left">
                                    <strong class="items-details-summary-name">{{ ag.name }}</strong>
                                </span>
                                <wa-button
                                    class="wf-agent-view"
                                    size="small"
                                    variant="brand"
                                    appearance="outlined"
                                    @click.stop="viewAgent(ag.agentId)"
                                >
                                    <wa-icon v-if="ag.active" slot="start" name="robot" class="wf-agent-running"></wa-icon>
                                    View Agent
                                </wa-button>
                            </span>
                            <div v-if="open.has('agent:' + ag.agentId)" class="wf-agent-body">
                                <div class="wf-info wf-agent-info">
                                    <span class="wf-info-item">
                                        <WorkflowStateBadge :kind="ag.statusKind" />
                                    </span>
                                    <span v-if="ag.durationMs != null" class="wf-info-item">
                                        <wa-icon name="stopwatch"></wa-icon>
                                        <span>{{ formatDuration(ag.durationMs / 1000) }}</span>
                                    </span>
                                    <span v-if="showCosts" class="wf-info-item">
                                        <CostDisplay :cost="ag.cost" />
                                    </span>
                                </div>
                                <wa-details
                                    v-if="ag.promptPreview"
                                    class="wf-row"
                                    icon-placement="start"
                                    :open="open.has('prompt:' + ag.agentId)"
                                    @wa-show="toggleOpen($event, 'prompt:' + ag.agentId, true)"
                                    @wa-hide="toggleOpen($event, 'prompt:' + ag.agentId, false)"
                                >
                                    <span slot="summary" class="items-details-summary">
                                        <span class="items-details-summary-left">
                                            <strong class="items-details-summary-name">Prompt</strong>
                                        </span>
                                    </span>
                                    <div v-if="open.has('prompt:' + ag.agentId)" class="wf-row-body wf-args-body">
                                        <MarkdownContent :source="ag.promptPreview" />
                                    </div>
                                </wa-details>
                                <wa-details
                                    v-if="ag.resultView"
                                    class="wf-row"
                                    icon-placement="start"
                                    :open="open.has('result:' + ag.agentId)"
                                    @wa-show="toggleOpen($event, 'result:' + ag.agentId, true)"
                                    @wa-hide="toggleOpen($event, 'result:' + ag.agentId, false)"
                                >
                                    <span slot="summary" class="items-details-summary">
                                        <span class="items-details-summary-left">
                                            <strong class="items-details-summary-name">Result</strong>
                                        </span>
                                    </span>
                                    <div v-if="open.has('result:' + ag.agentId)" class="wf-row-body wf-args-body">
                                        <JsonHumanView v-if="ag.resultView.mode === 'json'" :value="ag.resultView.value" />
                                        <MarkdownContent v-else :source="ag.resultView.value" />
                                    </div>
                                </wa-details>
                            </div>
                        </wa-details>
                    </div>
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

/* Section labels stand out from the content (was small + quiet — too discreet). */
.wf-section-label,
.wf-description-label {
    font-size: var(--wa-font-size-s);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
    color: var(--wa-color-text-normal);
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

.wf-row::part(content) {
    padding-block-start: 0;
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

/* Phase body: the info line + the nested agent rows. */
.wf-phase-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.wf-agents {
    display: flex;
    flex-direction: column;
}

/* Agent body: prompt + result previews, stacked. */
.wf-agent-body {
    display: flex;
    flex-direction: column;
}

.wf-agent-info {
    margin-bottom: var(--wa-space-xs);
}

/* "View Agent" button: pinned right, and kept from stretching the row (like the chat). */
.wf-agent .items-details-summary wa-button {
    margin-block: -0.4rem;
    margin-left: auto;
}

/* The "View Agent" robot pulses while the agent is still running, and is hidden
 * (v-if) once finished — the live "robot pulses while it works" cue
 * (ProcessIndicator's pulse, 1s). */
.wf-agent-running {
    animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
</style>
