<script setup>
import { ref, computed, watch, inject } from 'vue'
import { useDataStore } from '../../../stores/data'
import { useSettingsStore } from '../../../stores/settings'
import { formatDate } from '../../../utils/date'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES } from '../../../constants'
import { getProviderHelpers, getProviderLabel, getProviderIcon } from '../../../providers'
import { getAgentDisplayLabel } from '../../../utils/agentLabel'
import { stopSubagent } from '../../../composables/useWebSocket'
import { stopSessionProcess } from '../../../composables/useStopSessionProcess'
import ProjectBadge from '../../project/ProjectBadge.vue'
import WorktreeBadge from '../../project/WorktreeBadge.vue'
import ProcessIndicator from '../../ui/ProcessIndicator.vue'
import CodeCommentsIndicator from '../../ui/CodeCommentsIndicator.vue'
import ProcessDuration from '../../ui/ProcessDuration.vue'
import CostDisplay from '../../ui/CostDisplay.vue'
import AppTooltip from '../../ui/AppTooltip.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    mode: {
        type: String,
        default: 'session',
        validator: (value) => ['session', 'subagent'].includes(value)
    },
    /**
     * All available tabs for the compact-mode dropdown selector.
     * Each entry: { id, label, processState?, commentsCount? }
     */
    tabs: {
        type: Array,
        default: () => []
    },
    /**
     * ID of the currently active tab (matches a tab.id from the tabs prop).
     */
    activeTabId: {
        type: String,
        default: null
    }
})

const emit = defineEmits(['select-tab'])

const store = useDataStore()
const settingsStore = useSettingsStore()

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// Session data from store
const session = computed(() => store.getSession(props.sessionId))
// Whether the session's project is a git worktree of another project — drives
// the worktree-style title badge (parent name + branch icon + worktree folder),
// matching the project home header.
const isProjectWorktree = computed(() => !!store.getProject(session.value?.project_id)?.worktree_of)
// Whether the session's project is not trusted (effective trust ≠ trusted —
// explicitly untrusted or unknown). Drives the title-line lock marker, a
// session-level echo of the untrusted badge shown on project/worktree badges.
const isProjectUntrusted = computed(() => store.untrustedProjectIds.has(session.value?.project_id))
const providerLabel = computed(() => getProviderLabel(session.value?.provider))
const providerIcon = computed(() => getProviderIcon(session.value?.provider))

// Whether the session's provider is currently usable for runtime calls.
// Stricter than just intent-enabled: a provider in `starting` / `stopping`
// returns false too, matching the back gate.
const isProviderEnabled = computed(() => {
    const p = session.value?.provider
    return p ? store.isProviderAvailable(p) : true
})

// Get display name for header
// - Session mode: title if available, "New session" for drafts without title, otherwise session ID
// - Subagent mode: ``Agent <slug>`` when the provider exposes a slug
//   (Codex's agent_nickname); ``Agent <shortId>`` otherwise
const displayName = computed(() => {
    if (props.mode === 'subagent') {
        return `Agent ${getAgentDisplayLabel(props.sessionId, store)}`
    }
    // For draft sessions without a title, show "New session"
    if (session.value?.draft && !session.value?.title) {
        return 'New session'
    }
    return session.value?.title || props.sessionId
})

// Cost values for header display
const totalCost = computed(() => {
    const sess = session.value
    if (!sess) return null
    return sess.total_cost ?? null
})

// Cost breakdown (self + subagents) - only shown if subagents have cost
const costBreakdown = computed(() => {
    const sess = session.value
    if (!sess) return null

    const subagentsCost = sess.subagents_cost
    if (subagentsCost == null || subagentsCost <= 0) return null

    return {
        self: sess.self_cost ?? null,
        subagents: subagentsCost,
    }
})

// Calculate context usage percentage based on session's effective context_max
// (the store getter applies the auto-force-to-1M rule when usage exceeds 85%
// of the 200K window with no active process).
const contextMax = computed(() => store.getEffectiveContextMax(props.sessionId))

const contextUsagePercentage = computed(() => {
    const usage = session.value?.context_usage
    if (usage == null) return null
    return Math.round((usage / contextMax.value) * 100)
})

// Tooltip text for context usage ring. Resolve the choice label through
// the session's own provider helpers so non-Claude providers (Codex,
// future ones) can render their own ``context_max`` choice catalogue
// (e.g. "272K" for gpt-5). Falls back to a rounded "XK" label when the
// helper returns nothing — covers absent helpers and values not in the
// provider's choice list.
const contextUsageTooltip = computed(() => {
    const helpers = getProviderHelpers(session.value?.provider)
    const label = helpers?.getChoiceLabel('context_max', contextMax.value) || `${Math.round(contextMax.value / 1000)}K`
    return `Context window usage (${label} max)`
})

// Get indicator color for context usage based on thresholds
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-primary)'
})

// Calculate indicator width multiplier (1x at 0%, 2x at 80%+)
const contextUsageIndicatorWidth = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    // Linear interpolation from 1x (at 0%) to 1.5x (at 80%), capped at 1.5x
    const multiplier = Math.min(1 + (pct / 80), 1.5)
    return `calc(var(--track-width) * ${multiplier.toFixed(2)})`
})

// Display directory: git_directory if available, otherwise cwd. For a draft
// session — which has neither yet — fall back to the target project's path
// (git root if known, else its directory), i.e. where the session will run.
const displayDirectory = computed(() => {
    if (session.value?.git_directory) return session.value.git_directory
    if (session.value?.cwd) return session.value.cwd
    if (session.value?.draft) {
        const project = store.getProject(session.value?.project_id)
        return project?.git_root || project?.directory || null
    }
    return null
})

// Tooltip for directory: indicate whether it's the resolved git directory, the
// cwd fallback, or — for a draft — the target project's git root / directory.
const displayDirectoryTooltip = computed(() => {
    if (session.value?.git_directory) return 'Git working directory'
    if (session.value?.cwd) return 'Working directory (cwd)'
    if (session.value?.draft) {
        const project = store.getProject(session.value?.project_id)
        return project?.git_root ? 'Project git root' : 'Project directory'
    }
    return null
})

// Format model name for display from pre-parsed family and version
const formattedModel = computed(() => {
    const model = session.value?.model
    if (!model?.family || !model?.version) return null
    return `${model.family} ${model.version}`
})

// Process state for current session
const processState = computed(() => store.getProcessState(props.sessionId))

/** Whether the process has active cron jobs. */
const hasActiveCrons = computed(() => processState.value?.active_crons?.length > 0)

/** Number of active cron jobs (for tooltip). */
const activeCronCount = computed(() => processState.value?.active_crons?.length || 0)

/**
 * Get the color for a process state.
 * @param {string} state
 * @returns {string} CSS color variable
 */
function getProcessColor(state) {
    return PROCESS_STATE_COLORS[state] || PROCESS_STATE_COLORS[PROCESS_STATE.DEAD]
}

/**
 * Format memory in bytes to a human-readable string.
 * @param {number|null} bytes
 * @returns {string}
 */
function formatMemory(bytes) {
    if (bytes == null) return ''

    const kb = bytes / 1024
    const mb = kb / 1024
    const gb = mb / 1024

    if (gb >= 1) {
        return `${gb.toFixed(1)} GB`
    }
    if (mb >= 10) {
        return `${Math.round(mb)} MB`
    }
    if (mb >= 1) {
        return `${mb.toFixed(1)} MB`
    }
    return `${Math.round(kb)} KB`
}

// Only assistant_turn should animate
const animateStates = ['assistant_turn']

// Check if process can be stopped (any state except dead, and not a synthetic process state)
const canStopProcess = computed(() => {
    const ps = processState.value
    return ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
})

// Check if this is a background agent that can be stopped
const canStopAgent = computed(() => {
    if (props.mode !== 'subagent') return false
    const ps = processState.value
    if (!ps || !ps.synthetic || !ps.state || ps.state === PROCESS_STATE.DEAD) return false
    const parentId = session.value?.parent_session_id
    if (!parentId) return false
    const link = store.getAgentLinkByAgentId(parentId, props.sessionId)
    if (!link?.isBackground) return false
    // Provider opt-out for backends that don't (or can't) stop a
    // running subagent — see ``BaseProviderHelpers.canStopSubagent``.
    return !!getProviderHelpers(session.value?.provider)?.canStopSubagent()
})

// Track when a stop request has been sent and we're waiting for the process to die.
// Sourced from the store so it stays in sync across all UIs (sidebar, header, shortcut).
const stoppingProcess = computed(() => store.isSessionStopping(props.sessionId))
const stoppingAgent = ref(false)

// Reset stoppingAgent when the agent stops running
watch(canStopAgent, (canStop) => {
    if (!canStop) {
        stoppingAgent.value = false
    }
})

/**
 * Stop the current process. Delegates to the centralized composable, which
 * handles the active-crons confirmation and the stopping flag.
 */
function handleStopProcess() {
    if (stoppingProcess.value) return
    stopSessionProcess(props.sessionId)
}

/**
 * Stop the current agent via the SDK's stop_task.
 * No confirmation dialog needed for agents (no crons).
 */
function handleStopAgent() {
    const parentId = session.value?.parent_session_id
    if (canStopAgent.value && !stoppingAgent.value && parentId) {
        stoppingAgent.value = true
        stopSubagent(parentId, props.sessionId)
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// Compact header mode on small viewports
// ═══════════════════════════════════════════════════════════════════════════

// Track expanded state of the compact header overlay
const isCompactExpanded = ref(false)

// Active tab object (from the tabs prop, matched by activeTabId)
const activeTab = computed(() => props.tabs.find(t => t.id === props.activeTabId) || null)

/**
 * Handle tab selection from the compact dropdown.
 * Emits 'select-tab' with the selected tab ID.
 */
function handleCompactTabSelect(event) {
    const value = event.detail?.item?.value
    if (value && value !== props.activeTabId) {
        emit('select-tab', value)
    }
}

// Rename dialog (provided by ProjectView)
const injectedOpenRenameDialog = inject('openRenameDialog')

// Reference to the header element
const headerRef = ref(null)

/**
 * Open the rename dialog.
 * @param {Object} options
 * @param {boolean} options.showHint - Show contextual hint (when opened during message send)
 */
function openRenameDialog({ showHint = false } = {}) {
    if (session.value) {
        injectedOpenRenameDialog(session.value, { showHint })
    }
}

/**
 * Archive the current session.
 * Also stops the process if running — archived and running are mutually exclusive.
 * If the process has active crons, the composable shows the confirmation dialog.
 */
function handleArchive() {
    if (!session.value || session.value.archived || session.value.draft) return
    stopSessionProcess(props.sessionId, { archive: true })
}

/**
 * Unarchive the current session.
 */
function handleUnarchive() {
    if (session.value?.archived) {
        store.setSessionArchived(session.value.project_id, props.sessionId, false)
    }
}

/**
 * Label shown on the pin button tooltip, reflecting the current pin mode.
 */
const PIN_MODE_LABELS = { project: 'Project', workspace: 'Workspace', all: 'All projects' }
const pinTooltip = computed(() => {
    if (!session.value?.pinned) return 'Pin session'
    return `Pinned: ${PIN_MODE_LABELS[session.value.pinned] || session.value.pinned}`
})

/**
 * Handle pin mode selection from the dropdown.
 * @param {CustomEvent} event - The wa-select event (event.detail.item.value)
 */
function handlePinSelect(event) {
    if (!session.value || session.value.draft) return
    const value = event.detail.item.value
    const requested = value === 'none' ? null : value
    // Re-selecting the currently active mode toggles it off.
    const current = session.value.pinned || null
    const mode = requested !== null && requested === current ? null : requested
    store.setSessionPinMode(session.value.project_id, props.sessionId, mode)
}

// Expose methods and refs for parent components
defineExpose({
    openRenameDialog,
    headerRef,
    isCompactExpanded,
})
</script>

<template>
    <header ref="headerRef" class="session-header" :class="{ 'compact-expanded': isCompactExpanded, 'compact-collapsed': !isCompactExpanded }" :data-session-type="mode" v-if="session">
        <div v-if="mode === 'session'" class="session-title">
            <!-- Action buttons group: hidden in compact collapsed mode, replaced by active tab label -->
            <div class="session-title-actions">
                <wa-tag v-if="session.archived" :id="`session-header-${sessionId}-archived-tag`" size="small" variant="neutral" class="archived-tag" @click="handleUnarchive">Archived</wa-tag>
                <AppTooltip v-if="session.archived" :for="`session-header-${sessionId}-archived-tag`">Click to unarchive</AppTooltip>
                <wa-tag v-else-if="session.draft && !processState" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
                <wa-tag v-if="session.stale" :id="`session-header-${sessionId}-stale-tag`" size="small" variant="warning" class="stale-tag">Stale</wa-tag>
                <AppTooltip v-if="session.stale" :for="`session-header-${sessionId}-stale-tag`">Session files were deleted from disk</AppTooltip>

                <!-- Pin mode dropdown (not for drafts) -->
                <wa-dropdown
                    v-if="!session.draft"
                    class="pin-dropdown"
                    placement="bottom-start"
                    @wa-select="handlePinSelect"
                >
                    <wa-button
                        :id="`session-header-${sessionId}-pin-button`"
                        slot="trigger"
                        :variant="session.pinned ? 'brand' : 'neutral'"
                        appearance="plain"
                        size="small"
                        :class="['pin-button', 'reduced-height', { 'pin-button--active': session.pinned }]"
                    >
                        <wa-icon name="thumbtack" label="Pin"></wa-icon>
                    </wa-button>
                    <wa-dropdown-item type="checkbox" :checked="!session.pinned" value="none">
                        Not pinned
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'project'" value="project">
                        Pin in project
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'workspace'" value="workspace">
                        Pin in workspace
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'all'" value="all">
                        Pin everywhere
                    </wa-dropdown-item>
                </wa-dropdown>
                <AppTooltip v-if="!session.draft" :for="`session-header-${sessionId}-pin-button`">{{ pinTooltip }}</AppTooltip>

                <!-- Archive button (not for drafts or already archived) -->
                <wa-button
                    v-if="!session.archived && !session.draft"
                    :id="`session-header-${sessionId}-archive-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button reduced-height"
                    @click="handleArchive"
                >
                    <wa-icon name="box-archive" label="Archive"></wa-icon>
                </wa-button>
                <AppTooltip v-if="!session.archived && !session.draft" :for="`session-header-${sessionId}-archive-button`">{{ canStopProcess ? `Archive session (it will stop the ${providerLabel} process)` : 'Archive session' }}</AppTooltip>

                <!-- Rename button (only for main session) -->
                <wa-button
                    v-if="mode === 'session'"
                    :id="`session-header-${sessionId}-rename-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="rename-button reduced-height"
                    :disabled="!isProviderEnabled"
                    @click="openRenameDialog"
                >
                    <wa-icon name="pencil" label="Rename"></wa-icon>
                </wa-button>
                <AppTooltip :for="`session-header-${sessionId}-rename-button`">{{ isProviderEnabled ? 'Rename session' : 'Cannot rename: provider is disabled.' }}</AppTooltip>

                <!-- Pending request indicator (shown when waiting for user response) -->
                <wa-icon
                    v-if="store.getPendingRequests(sessionId).length > 0"
                    :id="`session-header-${sessionId}-pending-request`"
                    name="hand"
                    class="pending-request-indicator"
                ></wa-icon>
                <AppTooltip v-if="store.getPendingRequests(sessionId).length > 0" :for="`session-header-${sessionId}-pending-request`">Waiting for your response</AppTooltip>
            </div>

            <!-- Clickable zone: title + project + context ring + chevron toggle compact mode -->
            <div class="compact-toggle-zone" @click="isCompactExpanded = !isCompactExpanded">
                <!-- Compact tab dropdown: shown only in compact collapsed mode, replacing action buttons -->
                <wa-dropdown v-if="tabs.length" class="compact-tab-dropdown" placement="bottom-start" @wa-select="handleCompactTabSelect" @click.stop>
                    <wa-button slot="trigger" variant="brand" appearance="outlined" size="small" class="compact-active-tab-label" with-caret>
                        <span class="compact-active-tab-label-text">{{ activeTab?.label }}</span>
                        <ProcessIndicator
                            v-if="activeTab?.processState"
                            :state="activeTab.processState.state"
                            size="small"
                        />
                        <CodeCommentsIndicator :count="activeTab?.commentsCount || 0" :show-tooltip="false" class="compact-tab-comments-indicator" />
                    </wa-button>
                    <wa-dropdown-item
                        v-for="tab in tabs"
                        :key="tab.id"
                        :value="tab.id"
                        :class="{ 'active-tab-item': tab.id === activeTabId }"
                    >
                        <span class="compact-tab-dropdown-item-content">
                            <span>{{ tab.label }}</span>
                            <ProcessIndicator
                                v-if="tab.processState"
                                :state="tab.processState.state"
                                size="small"
                            />
                            <CodeCommentsIndicator :count="tab.commentsCount || 0" :show-tooltip="false" class="compact-tab-comments-indicator" />
                        </span>
                    </wa-dropdown-item>
                </wa-dropdown>

                <wa-icon
                    v-if="providerIcon"
                    auto-width
                    family="brands"
                    :name="providerIcon"
                    class="compact-provider-icon"
                ></wa-icon>

                <!-- Worktree marker: only when the session's project is a git worktree.
                     Sits right before the title; its tooltip restates that the session
                     runs in a worktree and embeds the same worktree badge shown elsewhere
                     (parent repo + branch icon + worktree folder). -->
                <wa-icon
                    v-if="isProjectWorktree"
                    :id="`session-header-${sessionId}-worktree`"
                    auto-width
                    name="code-branch"
                    class="worktree-title-icon"
                ></wa-icon>
                <AppTooltip v-if="isProjectWorktree" :for="`session-header-${sessionId}-worktree`">
                    <div class="worktree-title-tooltip">
                        <span>This session runs in a git worktree</span>
                        <WorktreeBadge :project-id="session.project_id" />
                    </div>
                </AppTooltip>

                <!-- Untrusted marker: only when the session's project is not trusted
                     (explicitly untrusted or unknown). Sits next to the worktree
                     marker as a session-level status flag; the project/worktree
                     badge on the right carries the same lock independently. -->
                <wa-icon
                    v-if="isProjectUntrusted"
                    :id="`session-header-${sessionId}-untrusted`"
                    auto-width
                    name="lock"
                    class="untrusted-title-icon"
                ></wa-icon>
                <AppTooltip v-if="isProjectUntrusted" :for="`session-header-${sessionId}-untrusted`">
                    This session is in an untrusted project
                </AppTooltip>

                <h2 :id="`session-header-${sessionId}-title`">{{ displayName }}</h2>
                <AppTooltip :for="`session-header-${sessionId}-title`">{{ displayName }}</AppTooltip>

                <router-link v-if="session.project_id" :to="{ name: 'project', params: { projectId: session.project_id } }" class="session-project" @click.stop>
                    <WorktreeBadge v-if="isProjectWorktree" :project-id="session.project_id" />
                    <ProjectBadge v-else :project-id="session.project_id" />
                </router-link>

                <!-- Context usage ring duplicate for compact mode (visible only on small viewports when not expanded) -->
                <wa-progress-ring
                    v-if="contextUsagePercentage != null"
                    class="context-usage-ring compact-context-ring"
                    :value="Math.min(contextUsagePercentage, 100)"
                    :style="{
                        '--indicator-color': contextUsageColor,
                        '--indicator-width': contextUsageIndicatorWidth
                    }"
                ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>

                <!-- Process state indicator duplicate for compact mode (visible only on small viewports when not expanded) -->
                <ProcessIndicator
                    v-if="processState"
                    class="compact-process-indicator"
                    :state="processState.state"
                    :has-active-crons="hasActiveCrons"
                    size="small"
                    :animate-states="animateStates"
                />

                <!-- Compact mode: expand/collapse chevron (only visible on small viewports via CSS) -->
                <wa-icon
                    v-if="!session.draft"
                    class="compact-toggle-chevron"
                    :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'"
                    label="Toggle details"
                ></wa-icon>
            </div>
        </div>

        <!-- Collapsible rows: git info + meta (overlay on small viewports) -->
        <div class="session-collapsible-rows">

            <!-- Git info row: directory @ branch. For a draft, displayDirectory
                 falls back to the project path and there is no branch yet, so
                 only the folder line shows. -->
            <div v-if="displayDirectory || session.git_branch" class="session-git-info">
                <span v-if="displayDirectory" :id="`session-header-${sessionId}-git-directory`" class="git-info-item">
                    <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                    <span>{{ displayDirectory }}</span>
                </span>
                <AppTooltip v-if="displayDirectory" :for="`session-header-${sessionId}-git-directory`">{{ displayDirectoryTooltip }}</AppTooltip>

                <span v-if="session.git_branch" :id="`session-header-${sessionId}-git-branch`" class="git-info-item">
                    <wa-icon auto-width name="code-branch"></wa-icon>
                    <span>{{ session.git_branch }}</span>
                </span>
                <AppTooltip v-if="session.git_branch" :for="`session-header-${sessionId}-git-branch`">Git branch</AppTooltip>
            </div>

            <!-- Meta row (not shown for draft sessions) -->
            <div v-if="!session.draft" class="session-meta">

                <span :id="`session-header-${sessionId}-messages`" class="meta-item">
                    <wa-icon auto-width name="comment" variant="regular"></wa-icon>
                    <span>{{ session.user_message_count ?? '??' }}</span>
                </span>
                <AppTooltip :for="`session-header-${sessionId}-messages`">Number of message turns</AppTooltip>

                <span :id="`session-header-${sessionId}-lines`" class="meta-item nb_lines">
                    <wa-icon auto-width name="bars"></wa-icon>
                    <span>{{ session.last_line }}</span>
                </span>
                <AppTooltip :for="`session-header-${sessionId}-lines`">Lines in the JSONL file</AppTooltip>

                <span :id="`session-header-${sessionId}-mtime`" class="meta-item">
                    <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                    <span>{{ formatDate(session.mtime, { smart: true }) }}</span>
                </span>
                <AppTooltip :for="`session-header-${sessionId}-mtime`">Last activity</AppTooltip>

                <template v-if="showCosts && totalCost != null">
                    <CostDisplay :id="`session-header-${sessionId}-cost`" :cost="totalCost" class="meta-item" />
                    <AppTooltip :for="`session-header-${sessionId}-cost`">Total session cost</AppTooltip>
                </template>

                <template v-if="showCosts && costBreakdown">
                    <span :id="`session-header-${sessionId}-cost-breakdown`" class="meta-item cost-breakdown-item">
                        <span>(
                        <span>
                            <CostDisplay :cost="costBreakdown.self" />
                            <span class="cost-breakdown-separator">+</span>
                            <CostDisplay :cost="costBreakdown.subagents" />
                        </span>
                        )</span>
                    </span>
                    <AppTooltip :for="`session-header-${sessionId}-cost-breakdown`">Main agent cost + sub-agents cost</AppTooltip>
                </template>

                <template v-if="formattedModel">
                    <span :id="`session-header-${sessionId}-model`" class="meta-item">
                        <wa-icon v-if="providerIcon" auto-width family="brands" :name="providerIcon"></wa-icon>
                        <wa-icon v-else auto-width name="robot" variant="classic"></wa-icon>
                        <span>{{ formattedModel }}</span>
                    </span>
                    <AppTooltip :for="`session-header-${sessionId}-model`">Last used model</AppTooltip>
                </template>

                <template v-if="contextUsagePercentage != null">
                    <wa-progress-ring
                        :id="`session-header-${sessionId}-context`"
                        class="context-usage-ring"
                        :value="Math.min(contextUsagePercentage, 100)"
                        :style="{
                            '--indicator-color': contextUsageColor,
                            '--indicator-width': contextUsageIndicatorWidth
                        }"
                    ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                    <AppTooltip :for="`session-header-${sessionId}-context`">{{ contextUsageTooltip }}</AppTooltip>
                </template>

                <template
                    v-if="processState"
                >
                    <div style="flex-grow: 1"></div>
                    <ProcessDuration
                        v-if="processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at"
                        :state-changed-at="processState.state_changed_at"
                        :id="`session-header-${sessionId}-process-duration`"
                        class="process-duration"
                        :style="{ color: getProcessColor(processState.state) }"
                    />
                    <AppTooltip v-if="processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at" :for="`session-header-${sessionId}-process-duration`">Assistant turn duration</AppTooltip>

                    <span
                        v-if="processState.memory"
                        :id="`session-header-${sessionId}-process-memory`"
                        class="process-memory"
                        :style="{ color: getProcessColor(processState.state) }"
                    >
                        {{ formatMemory(processState.memory) }}
                    </span>
                    <AppTooltip v-if="processState.memory" :for="`session-header-${sessionId}-process-memory`">{{ providerLabel }} memory usage</AppTooltip>

                    <ProcessIndicator
                        :id="`session-header-${sessionId}-process-indicator`"
                        :state="processState.state"
                        :has-active-crons="hasActiveCrons"
                        size="small"
                        :animate-states="animateStates"
                    />
                    <AppTooltip :for="`session-header-${sessionId}-process-indicator`">{{ providerLabel }} state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>

                    <wa-button
                        v-if="canStopProcess"
                        :id="`session-header-${sessionId}-stop-button`"
                        variant="danger"
                        appearance="filled"
                        size="small"
                        class="stop-button reduced-height"
                        :loading="stoppingProcess"
                        :disabled="stoppingProcess"
                        @click="handleStopProcess"
                    >
                        <wa-icon name="ban" label="Stop"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="`session-header-${sessionId}-stop-button`">Stop the {{ providerLabel }} process</AppTooltip>

                    <wa-button
                        v-if="canStopAgent"
                        :id="`session-header-${sessionId}-stop-agent-button`"
                        variant="danger"
                        appearance="filled"
                        size="small"
                        class="stop-button reduced-height"
                        :loading="stoppingAgent"
                        :disabled="stoppingAgent"
                        @click="handleStopAgent"
                    >
                        <wa-icon name="ban" label="Stop Agent"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="`session-header-${sessionId}-stop-agent-button`">Stop this agent</AppTooltip>
                </template>
            </div>

            <!-- Slot for extra compact-mode content (e.g. tab nav from SessionView) -->
            <slot name="compact-extra"></slot>

        </div><!-- /.session-collapsible-rows -->

        <wa-divider></wa-divider>

        <!-- Compact mode toggle for non main session headers (no .session-title row to host it) -->
        <wa-button
            v-if="mode !== 'session'"
            class="compact-toggle-button compact-toggle-button--non-main-session reduced-height"
            variant="neutral"
            appearance="plain"
            size="small"
            @click="isCompactExpanded = !isCompactExpanded"
        >
            <wa-icon :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'" label="Toggle details"></wa-icon>
        </wa-button>
    </header>

</template>

<style scoped>
.session-header {
    gap: var(--wa-space-xs);
    display: flex;
    flex-direction: column;
    background: var(--main-header-footer-bg-color);
    position: relative;
}

.session-title {
    flex: 1;
    display: flex;
    justify-content: start;
    align-items: baseline;
    gap: var(--wa-space-xs);
    min-width: 0;  /* Allow text truncation */
    padding-inline: var(--wa-space-xs);
    padding-top: var(--wa-space-xs);
}

/* Action buttons wrapper: transparent by default, hidden in compact collapsed mode */
.session-title-actions {
    display: contents;
}

/* Compact tab dropdown: hidden by default, shown in compact collapsed mode */
.compact-tab-dropdown {
    display: none;
}
.compact-active-tab-label {
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        font-size: var(--wa-font-size-s);
        display: flex;
        align-items: center;
        column-gap: var(--wa-space-xs);
    }
    .compact-active-tab-label-text {
        max-width: max(4rem, 20vw);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
}
.compact-tab-comments-indicator {
    font-size: var(--wa-font-size-xs);
    margin-left: var(--wa-space-2xs);
}
.compact-tab-dropdown-item-content {
    display: inline-flex;
    align-items: center;
    column-gap: var(--wa-space-xs);
}
.active-tab-item {
    opacity: 0.5;
    pointer-events: none;
}

.draft-tag, .archived-tag, .stale-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
}

.archived-tag {
    cursor: pointer;
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-normal);
    margin-right: var(--wa-space-xs);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-project {
    margin-left: auto;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    width: 25%;
    min-width: 3rem;
    max-width: max-content;
}
.session-project:hover {
    color: var(--wa-color-text);
}

/* Clickable zone for compact toggle: wraps title, project badge, context ring, and chevron */
.compact-toggle-zone {
    display: contents;
}

/* Compact chevron icon: hidden by default, shown only on small viewports */
.compact-toggle-chevron {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-xs);
    align-self: center;
}

.session-git-info {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-3xs);
    padding-inline: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    margin-top: var(--wa-space-xs);
}

.git-info-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-meta {
    display: flex;
    justify-content: start;
    align-items: center;
    gap: var(--wa-space-l);
    padding-inline: var(--wa-space-m);
}

.session-meta {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.cost-breakdown-item {
    gap: 0;
    > span {
        --parentheses-offset: 1.5px;
        position: relative;
        top: calc(-1 * var(--parentheses-offset));
        gap: 0.2em;
        > span {
            position: relative;
            top: var(--parentheses-offset);
        }
    }
}

.nb_lines, .cost-breakdown-item {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

body:not([data-display-mode="debug"]) .nb_lines,
body:not([data-display-mode="debug"]) .cost-breakdown-item {
    display: none;
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

/* Compact context ring: hidden by default, shown in compact mode when not expanded */
.compact-context-ring {
    display: none;
    align-self: center;
}

/* Compact process indicator: hidden by default, shown in compact mode when not expanded */
.compact-process-indicator {
    display: none;
    align-self: center;
}

/* Compact provider icon: hidden by default, shown in compact mode when not expanded */
.compact-provider-icon {
    display: none;
    align-self: center;
    flex-shrink: 0;
}

/* Worktree marker icon before the title (only for worktree projects). */
.worktree-title-icon {
    align-self: center;
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Untrusted-project marker before the title (sibling of the worktree marker).
   Matches it visually — full visibility, not the faint badge treatment — since
   it is a single focal session-status flag, not a list item. */
.untrusted-title-icon {
    align-self: center;
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Tooltip body: intro line stacked above the embedded worktree badge. */
.worktree-title-tooltip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.stop-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.stop-button:hover {
    opacity: 1;
}

.pin-button,
.archive-button,
.rename-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
    margin-block: calc(-3 * var(--wa-space-2xs));
    position: relative;
    top: calc(-1 * var(--wa-space-2xs));
}

/* The pin button lives inside a wa-dropdown; the dropdown itself is the flex child. */
.pin-dropdown {
    flex-shrink: 0;
}

.pin-button {
    &::part(label) {
        transform: rotate(30deg);
    }
    &.pin-button--active {
        opacity: 1;
        &::part(base) {
            color: var(--wa-color-yellow-80);
        }
    }
}

.pin-button:hover,
.archive-button:hover,
.rename-button:hover {
    opacity: 1;
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    animation: pending-pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
    align-self: center;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* ═══════════════════════════════════════════════════════════════════════════
   Compact header mode — toggle button + collapsible rows
   ═══════════════════════════════════════════════════════════════════════════ */

/* Toggle button: hidden by default, shown only on small viewports */
.compact-toggle-button {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    margin-block: calc(-3 * var(--wa-space-2xs));
    position: relative;
    top: calc(-1 * var(--wa-space-2xs));
}

.compact-toggle-button:hover {
    opacity: 1;
}

/* Non main session toggle: positioned absolutely below the header */
.compact-toggle-button--non-main-session {
    position: absolute;
    bottom: calc( -1 * var(--wa-space-xs));
    right: var(--wa-space-xs);
    transform: translateX(0) translateY(100%);
    z-index: 19;
    margin: 0;
    top: auto;
}

/* Collapsible rows wrapper: transparent on large viewports */
.session-collapsible-rows {
    display: contents;
}

@media (max-height: 900px) {
    /* Show the compact toggle chevron */
    .compact-toggle-chevron {
        display: inline-flex;
    }

    /* Show the compact toggle button for non-main sessions */
    .compact-toggle-button {
        display: inline-flex;
    }

    /* Make the toggle zone a clickable flex row */
    .compact-toggle-zone {
        display: flex;
        align-items: center;
        gap: var(--wa-space-s);
        min-width: 0;
        cursor: pointer;
        flex: 1;
    }

    .compact-toggle-zone:hover .compact-toggle-chevron {
        opacity: 1;
    }

    .draft-tag {
        margin-bottom: var(--wa-space-xs);
    }

    .session-header.compact-collapsed {
        border-bottom: solid var(--wa-color-surface-border) var(--divider-size);
    }

    /* In compact collapsed mode: hide action buttons, show tab dropdown */
    .session-header.compact-collapsed .session-title-actions {
        display: none;
    }
    .session-header.compact-collapsed .compact-tab-dropdown {
        display: inline-flex;
        align-items: center;
    }

    /* In compact expanded mode: show action buttons, hide tab dropdown */
    .session-header.compact-expanded .compact-tab-dropdown {
        display: none;
    }

    /* Dont show divider when compact mode is active */
    .session-header wa-divider {
        display: none;
    }

    /* Add some padding on the bottom of the first line */
    .session-header.compact-collapsed .session-title {
        padding-bottom: var(--wa-space-xs);
        align-items: center;
    }

    /* Show the compact context ring when not expanded */
    .session-header.compact-collapsed .compact-context-ring {
        display: inline-flex;
    }

    /* Show the compact process indicator when not expanded */
    .session-header.compact-collapsed .compact-process-indicator {
        display: inline-flex;
    }

    /* Show the compact provider icon when not expanded */
    .session-header.compact-collapsed .compact-provider-icon {
        display: inline-flex;
    }

    /* Collapsible rows become an overlay panel */
    .session-collapsible-rows {
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-xs);
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        z-index: 20;
        background: var(--wa-color-surface-default);
        box-shadow: var(--wa-shadow-s);
        border-bottom: solid var(--wa-color-surface-border) var(--divider-size);

        /* Hidden by default */
        opacity: 0;
        visibility: hidden;
        transform: translateY(-8px);
        transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s;
    }
    .session-header:not([data-session-type="session"]) .session-collapsible-rows {
        z-index: 19;
    }


    /* When expanded: reveal the overlay */
    .session-header.compact-expanded .session-collapsible-rows {
        opacity: 1;
        visibility: visible;
        transform: translateY(0);
    }

    .session-header.compact-expanded .compact-toggle-button--non-main-session {
        bottom: -100%;
    }

}

</style>
