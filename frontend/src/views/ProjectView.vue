<script setup>
import { computed, ref, watch, onMounted, onUnmounted, onBeforeUnmount, provide, nextTick } from 'vue'
import { useElementHover } from '@vueuse/core'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useWorkspacesStore } from '../stores/workspaces'
import { COLOR_SCHEME } from '../constants'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { useStartupPolling } from '../composables/useStartupPolling'
import { useToast } from '../composables/useToast'
import { useProviderActivation } from '../composables/useProviderActivation'
import { ensureProjectTrust } from '../composables/useTrustGate'
import { useTerminalCommandStore } from '../stores/terminalCommand'
import { useSessionSelectionStore } from '../stores/sessionSelection'
import { getRegisteredProviders, getProviderHelpers, getProviderStore, getProviderLabel, getProviderIcon } from '../providers'
import { toWorkspaceProjectId } from '../utils/workspaceIds'
import { splitProjectsByPriority } from '../utils/projectSort'
import SessionList from '../components/session/list/SessionList.vue'
import SessionSelectionBar from '../components/session/list/SessionSelectionBar.vue'
import FetchErrorPanel from '../components/ui/FetchErrorPanel.vue'
import SettingsPopover from '../components/app/SettingsPopover.vue'
import CommandPaletteButton from '../components/app/CommandPaletteButton.vue'
import ProjectBadge from '../components/project/ProjectBadge.vue'
import ProjectSelectorRow from '../components/project/ProjectSelectorRow.vue'
import WorktreeSelectorRows from '../components/project/WorktreeSelectorRows.vue'
import WorktreePickerRows from '../components/project/WorktreePickerRows.vue'
import WorktreeBadge from '../components/project/WorktreeBadge.vue'
import WorktreeButton from '../components/project/WorktreeButton.vue'
import WorktreeDialog from '../components/project/WorktreeDialog.vue'
import ProjectDetailPanel from '../components/project/ProjectDetailPanel.vue'
import SessionRenameDialog from '../components/session/detail/SessionRenameDialog.vue'
import ProjectEditDialog from '../components/project/ProjectEditDialog.vue'
import WorkspaceManageDialog from '../components/workspace/WorkspaceManageDialog.vue'
import BulkArchiveConfirmDialog from '../components/sidebar/BulkArchiveConfirmDialog.vue'
import { getUsageRingColor, formatRecentDelta } from '../utils/usage'
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'
import { projectPathTitle } from '../utils/projectName'
import CostDisplay from '../components/ui/CostDisplay.vue'
import AppTooltip from '../components/ui/AppTooltip.vue'
import UsageGraphDialog from '../components/app/UsageGraphDialog.vue'
import AggregatedProcessIndicator from '../components/ui/AggregatedProcessIndicator.vue'
import CodeCommentsIndicator from '../components/ui/CodeCommentsIndicator.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const { registerCommands, unregisterCommands } = useCommandRegistry()

// Poll home data during startup so sparklines and project stats update
// as sessions are indexed by background compute.
useStartupPolling(() => store.loadHomeData())

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// In light mode the tooltip background is dark (WA inverts it), so buttons need a filled
// appearance to be readable on that dark background. In dark mode the tooltip is light and
// outlined buttons work fine.
const quotaButtonAppearance = computed(() =>
    settingsStore.getEffectiveColorScheme === COLOR_SCHEME.DARK ? 'outlined' : 'filled'
)

// ═══════════════════════════════════════════════════════════════════════════
// Provider authentication callouts
// ═══════════════════════════════════════════════════════════════════════════

const terminalCommandStore = useTerminalCommandStore()
const selectionStore = useSessionSelectionStore()

// Reactive set of providers currently waiting for an auth recheck round-trip
// (so each "Check again" button can disable independently).
const authChecking = ref(new Set())

// Providers that the registry knows about and that gate sending on auth.
// Registry membership is fixed at app boot, so this is a plain array.
const _authAwareProviders = getRegisteredProviders()
    .map(provider => ({ provider, helpers: getProviderHelpers(provider) }))
    .filter(({ helpers }) => helpers.getAuthState() !== null)

const { canDisableProvider, setProviderEnabled } = useProviderActivation()

const unauthenticatedProviders = computed(() => {
    const enabled = new Set(settingsStore.enabledProviders)
    const result = []
    for (const { provider, helpers } of _authAwareProviders) {
        if (!enabled.has(provider)) continue
        const authStateGetter = helpers.getAuthState()
        if (authStateGetter() !== false) continue
        result.push({
            provider,
            label: helpers.constructor.label,
            loginCommand: helpers.getAuthLoginCommand(),
            canDisable: canDisableProvider(provider),
        })
    }
    return result
})

function recheckProviderAuth(provider) {
    if (authChecking.value.has(provider)) return
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    authChecking.value.add(provider)
    helpers.requestAuthRecheck()
    setTimeout(() => {
        authChecking.value.delete(provider)
        // Force reactivity: ``Set`` mutation alone doesn't trigger watchers.
        authChecking.value = new Set(authChecking.value)
    }, 1500)
}

function launchProviderAuthInTerminal(loginCommand) {
    terminalCommandStore.request('global', loginCommand)
    router.push({ name: 'projects-terminal' })
}

function disableProvider(provider) {
    setProviderEnabled(provider, false)
    toast.success(`${getProviderLabel(provider)} provider disabled`, { duration: 2000 })
    // Callout disappears on its own: the provider leaves ``enabledProviders``,
    // which empties its slot in ``unauthenticatedProviders``.
}

function copyLoginCommand(loginCommand) {
    navigator.clipboard.writeText(loginCommand)
    toast.success('Command copied to clipboard', { duration: 2000 })
}

// ═══════════════════════════════════════════════════════════════════════════
// Usage quotas — rotation across providers that expose a quota
// ═══════════════════════════════════════════════════════════════════════════
//
// Providers opt in via ``helpers.tracksUsage()``. The sidebar slot rotates
// among them every ``USAGE_ROTATION_INTERVAL_MS``; the rotation is paused
// while the user hovers the block (which is also when wa-tooltip's hover
// trigger keeps a tooltip open) so the readings don't shift under the
// user's eyes mid-inspection.

const USAGE_ROTATION_INTERVAL_MS = 15000

const usageProviders = computed(() => {
    const enabled = new Set(settingsStore.enabledProviders)
    return getRegisteredProviders().filter(
        provider => enabled.has(provider) && getProviderHelpers(provider).tracksUsage()
    )
})

const currentUsageProviderIndex = ref(0)

const currentUsageProvider = computed(() => {
    const list = usageProviders.value
    if (list.length === 0) return null
    return list[currentUsageProviderIndex.value % list.length]
})

const currentUsageHelpers = computed(() => currentUsageProvider.value ? getProviderHelpers(currentUsageProvider.value) : null)
const currentUsageStore = computed(() => currentUsageProvider.value ? getProviderStore(currentUsageProvider.value) : null)
const currentUsageProviderIcon = computed(() => currentUsageProvider.value ? getProviderIcon(currentUsageProvider.value) : null)
const currentUsageProviderLabel = computed(() => currentUsageProvider.value ? getProviderLabel(currentUsageProvider.value) : null)
const hasMultipleUsageProviders = computed(() => usageProviders.value.length > 1)
const usageExternalLink = computed(() => currentUsageHelpers.value?.getUsageExternalLink() ?? null)

const usageBlockRef = ref(null)
const isUsageBlockHovered = useElementHover(usageBlockRef)

let _usageRotationTimer = null
function _scheduleUsageRotation() {
    if (_usageRotationTimer) clearInterval(_usageRotationTimer)
    _usageRotationTimer = setInterval(() => {
        if (isUsageBlockHovered.value) return
        const total = usageProviders.value.length
        if (total <= 1) return
        currentUsageProviderIndex.value = (currentUsageProviderIndex.value + 1) % total
    }, USAGE_ROTATION_INTERVAL_MS)
}
function cycleUsageProvider() {
    const total = usageProviders.value.length
    if (total <= 1) return
    currentUsageProviderIndex.value = (currentUsageProviderIndex.value + 1) % total
    // Restart the timer so the freshly selected provider gets the full
    // rotation interval rather than whatever remained on the previous tick.
    _scheduleUsageRotation()
}
onMounted(() => {
    _scheduleUsageRotation()
})
onBeforeUnmount(() => {
    if (_usageRotationTimer) clearInterval(_usageRotationTimer)
})

const quotaData = computed(() => currentUsageStore.value?.usage ?? null)
const quotaComputed = computed(() => quotaData.value?.computed ?? null)
// Show the usage block whenever usage data is available — the source can be
// OAuth credentials or a local JSON file; either way the backend signals
// availability by sending usage data (raw=null when no source).
const quotaHasUsage = computed(() => !!quotaData.value?.raw)

const quotaFiveHour = computed(() => quotaComputed.value?.fiveHour ?? null)
const quotaSevenDay = computed(() => quotaComputed.value?.sevenDay ?? null)

const quotaExtraUsage = computed(() => {
    const extra = quotaComputed.value?.extraUsage
    if (!extra || !extra.isEnabled) return null
    // "Only when needed" mode: show if a standard quota is saturated, OR if
    // extra usage was consumed in the last ~hour (fast mode can burn extra
    // credits mid-period before the 5h / 7d quotas reach 100%).
    if (settingsStore.isExtraUsageOnlyWhenNeeded) {
        const fh = quotaFiveHour.value
        const sd = quotaSevenDay.value
        const saturated = (fh && fh.utilization >= 100) || (sd && sd.utilization >= 100)
        if (!saturated && !extra.recentlyActive) return null
    }
    return extra
})

const quotaFiveHourCost = computed(() => quotaComputed.value?.fiveHourCost ?? null)
const quotaSevenDayCost = computed(() => quotaComputed.value?.sevenDayCost ?? null)

const quotaFiveHourRingColor = computed(() => getUsageRingColor(quotaFiveHour.value))
const quotaSevenDayRingColor = computed(() => getUsageRingColor(quotaSevenDay.value))
const quotaExtraUsageRingColor = computed(() => {
    const extra = quotaExtraUsage.value
    if (!extra) return 'var(--wa-color-neutral)'
    // Remaining-only mode (Codex): no utilization to tier on — color reflects
    // whether there's any balance left, not how much of a limit was consumed.
    if (extra.utilization == null && extra.remainingCredits != null) {
        return extra.remainingCredits > 0 ? 'var(--wa-color-success)' : 'var(--wa-color-danger)'
    }
    if (extra.utilization == null) return 'var(--wa-color-neutral)'
    if (extra.utilization >= 75) return 'var(--wa-color-danger)'
    if (extra.utilization >= 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-success)'
})

const quotaExtraUsageRingValue = computed(() => {
    const extra = quotaExtraUsage.value
    if (!extra || extra.utilization == null) return 0
    return Math.min(extra.utilization, 100)
})

const quotaExtraUsageRingLabel = computed(() => {
    const extra = quotaExtraUsage.value
    if (!extra) return ''
    if (extra.utilization != null) return `${Math.round(extra.utilization)}%`
    if (extra.remainingCredits != null) return String(Math.round(extra.remainingCredits))
    return ''
})

function resetsAtToDate(resetsAt) {
    if (!resetsAt) return new Date()
    return new Date(resetsAt)
}

function extraUsageResetDate() {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth() + 1, 1)
}

function formatResetTime(resetsAt) {
    if (!resetsAt) return '?'
    const reset = resetsAt instanceof Date ? resetsAt : new Date(resetsAt)
    const now = new Date()
    const locale = navigator.language
    const diffMs = reset - now
    const diffHours = diffMs / (1000 * 60 * 60)
    // < 24h: time only
    if (diffHours < 24) {
        return reset.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    }
    // < 7 days: weekday + time
    if (diffHours < 7 * 24) {
        return reset.toLocaleDateString(locale, { weekday: 'long', hour: '2-digit', minute: '2-digit' })
    }
    // >= 7 days: weekday + day/month
    return reset.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'numeric' })
}

function formatResetTimePrecise(resetsAt) {
    if (!resetsAt) return ''
    const reset = resetsAt instanceof Date ? resetsAt : new Date(resetsAt)
    const locale = navigator.language
    return reset.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })
}

// Stale data detection: data older than 15 minutes
const STALE_THRESHOLD_MS = 15 * 60 * 1000
const STALE_CHECK_INTERVAL_MS = 60 * 1000 // Re-check every minute

// Reactive "now" that ticks every minute to trigger stale re-evaluation
const now = ref(Date.now())
const staleCheckInterval = setInterval(() => { now.value = Date.now() }, STALE_CHECK_INTERVAL_MS)
onUnmounted(() => clearInterval(staleCheckInterval))

const quotaIsStale = computed(() => {
    const fetchedAt = quotaComputed.value?.fetchedAt
    if (!fetchedAt) return false
    const fetchedDate = new Date(fetchedAt)
    return (now.value - fetchedDate.getTime()) > STALE_THRESHOLD_MS
})

const quotaLastUpdateFormatted = computed(() => {
    const fetchedAt = quotaComputed.value?.fetchedAt
    if (!fetchedAt) return '?'
    const date = new Date(fetchedAt)
    const locale = navigator.language
    return date.toLocaleString(locale, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
})


// ═══════════════════════════════════════════════════════════════════════════
// Usage Graph Dialog
// ════════════════════════════════════���══════════════════════════════════════

const usageGraphDialogRef = ref(null)

function openUsageGraph(period) {
    usageGraphDialogRef.value?.open(period)
}

// ═══════════════════════════════════════════════════════════════════════════
// Manual usage refresh (Claude Code only)
// ═══════════════════════════════════════════════════════════════════════════
//
// On macOS the background loop never auto-refreshes the OAuth token (it would
// rewrite the Keychain item and pop an unprompted authorization dialog), so the
// usage data goes stale once the token expires. The "Refresh now" button in the
// stale-warning tooltip lets the user trigger the refresh explicitly — any
// Keychain prompt then follows a deliberate click. The button shows a spinner
// until the matching usage_updated (reason === 'manual') comes back.

// Manual usage refresh is a per-provider capability: the helper exposes
// ``supportsUsageRefresh()`` (gates the "Refresh now" button) and
// ``requestUsageRefresh()`` (fires the provider's own check_usage WS action,
// lazy-importing its ws module to avoid an import cycle). Both Claude Code and
// Codex opt in; the button targets whichever provider is currently displayed.
const canRefreshUsage = computed(() => currentUsageHelpers.value?.supportsUsageRefresh() ?? false)
const usageRefreshing = computed(() => currentUsageStore.value?.usageRefreshing ?? false)

// Safety net: clear the spinner if no manual usage_updated arrives (e.g. a WS
// hiccup) within the backend's worst-case refresh window (SDK refresh timeout
// is 30s; 40s leaves margin).
const USAGE_REFRESH_SAFETY_MS = 40000
let _usageRefreshTimer = null

async function refreshUsage() {
    const helpers = currentUsageHelpers.value
    const store = currentUsageStore.value
    if (!helpers || !store || store.usageRefreshing || !helpers.supportsUsageRefresh()) return
    store.setUsageRefreshing(true)
    if (_usageRefreshTimer) clearTimeout(_usageRefreshTimer)
    _usageRefreshTimer = setTimeout(() => store.setUsageRefreshing(false), USAGE_REFRESH_SAFETY_MS)
    await helpers.requestUsageRefresh()
}

onBeforeUnmount(() => {
    if (_usageRefreshTimer) clearTimeout(_usageRefreshTimer)
})

// ═══════════════════════════════════════════════════════════════════════════
// Shared Rename Dialog (single instance for both sidebar and session header)
// ═══════════════════════════════════════════════════════════════════════════

const renameDialogRef = ref(null)
const sessionToRename = ref(null)

/**
 * Open the rename dialog for a session.
 * Provided to child components via provide/inject.
 * @param {Object} session - The session object to rename
 * @param {Object} [options] - Options passed to the dialog's open method
 * @param {boolean} [options.showHint] - Show contextual hint (for needs-title flow)
 */
function openRenameDialog(session, options = {}) {
    sessionToRename.value = session
    renameDialogRef.value?.open({ ...options, session })
}

provide('openRenameDialog', openRenameDialog)

// Pending drop data: set when files/text are dropped on a session list item.
// SessionView watches this and forwards to SessionItemsList once mounted.
const pendingDropData = ref(null)
provide('pendingDropData', pendingDropData)

// Current project from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId || null)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

// Workspace state
const workspacesStore = useWorkspacesStore()
const activeWorkspaceId = computed(() => route.query.workspace || null)
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)
const isWorkspaceMode = computed(() => isAllProjectsMode.value && !!activeWorkspaceId.value)
const workspaceVisibleProjectIds = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getVisibleProjectIds(activeWorkspaceId.value) : []
)
// Same set, minus worktrees — used only for the workspace *project list* shown
// in the sidebar selector (worktrees are surfaced nested under their parent).
// workspaceVisibleProjectIds keeps the worktrees: it scopes the workspace's
// sessions and drives show-project-name, since a member's worktree sessions
// count toward the whole.
const workspaceSelectorProjectIds = computed(() =>
    workspaceVisibleProjectIds.value.filter(pid => !store.getProject(pid)?.worktree_of)
)

// Show the per-session project badge when the list aggregates more than one
// project: all-projects mode (outside a single-project workspace), or a single
// project that has visible worktrees (their sessions are mixed in, so the badge
// tells parent vs worktree sessions apart).
const showProjectNameInList = computed(() => {
    if (isAllProjectsMode.value) {
        return !activeWorkspace.value || workspaceVisibleProjectIds.value.length > 1
    }
    return visibleWorktreesOf(projectId.value).length > 0
})

// --- Worktree entries in the sidebar project selector --------------------
// A project with worktrees gets a collapsible "Worktrees (N)" entry nested
// under it in the selector list. Expansion state is ephemeral (not persisted);
// the parent of the currently-open worktree is auto-expanded (watch below).
const expandedWorktrees = ref(new Set())
function isWorktreesExpanded(id) {
    return expandedWorktrees.value.has(id)
}
function toggleWorktrees(id) {
    if (expandedWorktrees.value.has(id)) {
        expandedWorktrees.value.delete(id)
    } else {
        expandedWorktrees.value.add(id)
    }
}
/** Worktrees of a project visible under the current archived setting (count + nested list). */
function visibleWorktreesOf(id) {
    return store.getWorktreesOf(id).filter(p => showArchivedProjects.value || !p.archived)
}
// Auto-expand a project's worktrees entry when the current project is one of
// its worktrees, so the open worktree is visible in the selector. We watch the
// resolved parent id (not just projectId) so it also fires once project data
// finishes loading on a direct deep-link/reload into a worktree.
watch(() => store.getProject(projectId.value)?.worktree_of, (parent) => {
    if (parent) expandedWorktrees.value.add(parent)
}, { immediate: true })

// --- Worktree entries in the "New session" project pickers ----------------
// Same collapsible "Worktrees (N)" entry as the navigation selector above, but
// for the two "New session" project pickers (the split-button dropdown in
// single-project mode and the standalone dropdown in all-projects mode — only
// one is rendered at a time, so they share one expansion set). Expansion is
// ephemeral and starts collapsed: these pickers choose where to create a
// session, so there is no "current" worktree to auto-expand.
const newSessionExpandedWorktrees = ref(new Set())
function isNewSessionWorktreesExpanded(id) {
    return newSessionExpandedWorktrees.value.has(id)
}
function toggleNewSessionWorktrees(id) {
    if (newSessionExpandedWorktrees.value.has(id)) {
        newSessionExpandedWorktrees.value.delete(id)
    } else {
        newSessionExpandedWorktrees.value.add(id)
    }
}
/**
 * Worktrees of a project that can host a new session: same archived rule as the
 * picker's parent projects, and stale worktrees excluded (their directory is
 * gone — you cannot create a session there), mirroring `nonStaleProjects`.
 */
function pickableWorktreesOf(id) {
    return store.getWorktreesOf(id).filter(p => (showArchivedProjects.value || !p.archived) && !p.stale)
}

const activeWsLabel = computed(() =>
    activeWorkspace.value ? `${activeWorkspace.value.name} projects` : null
)

// Effective project ID for store operations
const effectiveProjectId = computed(() => {
    if (!isAllProjectsMode.value) return projectId.value
    if (activeWorkspaceId.value) return toWorkspaceProjectId(activeWorkspaceId.value)
    return ALL_PROJECTS_ID
})

// All projects for the selector (already sorted by mtime desc in store).
// Worktree projects are excluded from every project picker/selector.
const allProjects = computed(() =>
    store.getListableProjects.filter(p => showArchivedProjects.value || !p.archived)
)
// Non-stale projects only — used in "new session" dropdowns to prevent creating sessions in stale projects
const nonStaleProjects = computed(() => allProjects.value.filter(p => !p.stale))
const nonStaleNamedProjects = computed(() =>
    nonStaleProjects.value.filter(p => p.name !== null)
)
const nonStaleFlatTree = computed(() => {
    const unnamed = nonStaleProjects.value.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})

// Workspace-first split for "New session" dropdowns.
// When a workspace is active, workspace projects appear first, then others after a divider.
const wsVisibleSet = computed(() =>
    activeWorkspaceId.value ? new Set(workspaceVisibleProjectIds.value) : null
)
const wsPriorityIds = computed(() =>
    activeWorkspace.value ? activeWorkspace.value.projectIds : null
)
const splitNamedProjects = computed(() =>
    splitProjectsByPriority(nonStaleNamedProjects.value, wsPriorityIds.value, wsVisibleSet.value)
)
const splitFlatTree = computed(() => {
    const unnamed = nonStaleProjects.value.filter(p => p.name === null)
    if (!wsPriorityIds.value) {
        return { prioritized: [], others: flattenProjectTree(buildProjectTree(unnamed)) }
    }
    const { prioritized: priProjects, others: otherProjects } = splitProjectsByPriority(unnamed, wsPriorityIds.value, wsVisibleSet.value)
    return {
        prioritized: flattenProjectTree(buildProjectTree(priProjects)),
        others: flattenProjectTree(buildProjectTree(otherProjects)),
    }
})

// Projects not in the active workspace (for the "other" section in the dropdown)
const otherProjectsOutsideWorkspace = computed(() => {
    if (!activeWorkspaceId.value) return []
    const wsSet = new Set(workspaceVisibleProjectIds.value)
    return allProjects.value.filter(p => !wsSet.has(p.id))
})

// Named/unnamed split for the sidebar project selector dropdown (all projects, or "other" projects when workspace active)
const selectorNamedProjects = computed(() =>
    allProjects.value.filter(p => p.name !== null)
)
const selectorFlatTree = computed(() => {
    const unnamed = allProjects.value.filter(p => p.name === null)
    return flattenProjectTree(buildProjectTree(unnamed))
})
const otherNamedProjects = computed(() =>
    otherProjectsOutsideWorkspace.value.filter(p => p.name !== null)
)
const otherFlatTree = computed(() => {
    const unnamed = otherProjectsOutsideWorkspace.value.filter(p => p.name === null)
    if (!unnamed.length) return []
    return flattenProjectTree(buildProjectTree(unnamed))
})

// Whether the current project (single-project mode) is stale
const isCurrentProjectStale = computed(() => {
    if (isAllProjectsMode.value) return false
    const project = store.getProject(projectId.value)
    return project?.stale ?? false
})

// Selected project color for display in the closed select
const selectedProjectColor = computed(() => {
    if (isAllProjectsMode.value) return null
    const project = store.getProject(projectId.value)
    if (!project) return null
    // A worktree inherits its main repository's color when it has none of its own.
    if (project.worktree_of) {
        return project.color || store.getProject(project.worktree_of)?.color || null
    }
    return project.color || null
})

// Whether the current single project is a git worktree. When so, the selector
// trigger renders a <WorktreeBadge> ("<main repo> ⎇ <worktree folder>") instead
// of the plain label below.
const isCurrentProjectWorktree = computed(() => !!store.getProject(projectId.value)?.worktree_of)

// Label shown in the selector trigger for a regular (non-worktree) single project.
const selectedProjectLabel = computed(() => store.getProjectDisplayName(projectId.value))
// Full directory path on hover when that project is unnamed (shown by folder name only).
const selectedProjectTitle = computed(() => projectPathTitle(store.getProject(projectId.value)))

// Loading and error states for sessions
// Only show initial loading spinner when we haven't fetched any sessions yet
const areSessionsFetched = computed(() => store.areProjectSessionsFetched(effectiveProjectId.value))
const isInitialLoading = computed(() =>
    store.areSessionsLoading(effectiveProjectId.value) && !areSessionsFetched.value
)
const didSessionsFailToLoad = computed(() => store.didSessionsFailToLoad(effectiveProjectId.value))

// Search/filter state for sessions
const searchQuery = ref('')
const trimmedSearchQuery = computed(() => searchQuery.value.trim())

// Show archived sessions filter (persistent setting, browser-local via settings store)
const showArchivedSessions = computed(() => settingsStore.isShowArchivedSessions)

// Show archived projects filter (persistent setting, browser-local via settings store)
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)

// Compact view (persistent setting, browser-local via settings store)
const compactView = computed(() => settingsStore.isCompactSessionList)

// Always-show-active filter (persistent setting): promotes running/unread
// sessions from other projects to the top of the sidebar regardless of the
// current filter.
const showActiveAcrossFilters = computed(() => settingsStore.isShowActiveAcrossFilters)

// Reference to SessionList for keyboard navigation
const sessionListRef = ref(null)

// Reference to the search input for focus management
const searchInputRef = ref(null)

/**
 * Focus the search input field.
 * Called when SessionList emits 'focus-search' (e.g., ArrowUp from first item).
 */
function focusSearchInput() {
    searchInputRef.value?.focus()
}

// Clear search when project changes
watch(effectiveProjectId, () => {
    searchQuery.value = ''
})

/**
 * Handle keyboard events from the search input.
 * Only delegates ArrowDown, Enter, and Escape to the SessionList component.
 * All other keys (Home, End, PageUp, PageDown, ArrowUp) keep their default
 * text input behavior so the user can navigate within the search field.
 *
 * @param {KeyboardEvent} event
 */
function handleSearchKeydown(event) {
    // From the search input, only these keys should be delegated to SessionList.
    // Other navigation keys (Home, End, PageUp, PageDown, ArrowUp) must keep
    // their default text input behavior (cursor movement, text selection, etc.).
    const navigationKeys = ['ArrowDown', 'Enter', 'Escape']

    if (navigationKeys.includes(event.key) && sessionListRef.value) {
        const handled = sessionListRef.value.handleKeyNavigation(event, { fromSearch: true })
        if (handled) {
            event.preventDefault()
            return
        }
    }

    // Escape not handled by SessionList (no highlight) → clear search field
    if (event.key === 'Escape' && searchQuery.value) {
        searchQuery.value = ''
        event.preventDefault()
    }
}

function openAdvancedSearch() {
    window.dispatchEvent(new CustomEvent('twicc:open-search'))
}

// Load sessions when project changes or mode changes. Also refresh the sticky
// set (pinned / unread / running-process sessions across every project) so
// newly activated sessions in other projects surface in the sidebar without
// waiting for a page reload — the initial App.vue load only fires once at
// auth, so it may have raced or missed HMR updates.
watch(effectiveProjectId, async (newProjectId) => {
    if (newProjectId) {
        await store.loadSessions(newProjectId, { isInitialLoading: true })
    }
    store.loadStickySessions()
}, { immediate: true })

// When the user is browsing a workspace and the workspace's project set
// changes (project added/removed via WorkspaceManageDialog, archived flag
// toggled, project auto-added by the backend after a JSONL appears), the
// effectiveProjectId stays the same so the loader above doesn't refire.
// Re-fetch sessions so the sidebar reflects pre-existing sessions of any
// newly-included project. Key is the sorted id list, so set-equal recomputes
// (which are frequent on workspace store mutations) don't trigger a refetch.
watch(
    () => isWorkspaceMode.value
        ? workspaceVisibleProjectIds.value.slice().sort().join(',')
        : null,
    (newKey, oldKey) => {
        if (newKey == null || oldKey == null || newKey === oldKey) return
        store.loadSessions(effectiveProjectId.value, { force: true })
    },
)

// Same idea one level down: while staying on a main repository, its set of
// worktrees can change (a worktree project is synced/created), without
// effectiveProjectId changing. Re-fetch so the new worktree's sessions surface.
// Keyed on the sorted scope ids; null when the project has no worktrees.
const singleProjectScopeKey = computed(() => {
    if (isAllProjectsMode.value || !projectId.value) return null
    const ids = store.getProjectScopeIds(projectId.value)
    return ids.length > 1 ? ids.slice().sort().join(',') : null
})
watch(singleProjectScopeKey, (newKey, oldKey) => {
    if (newKey == null || oldKey == null || newKey === oldKey) return
    store.loadSessions(effectiveProjectId.value, { force: true })
})

// Retry loading sessions
async function handleRetry() {
    if (effectiveProjectId.value) {
        await store.loadSessions(effectiveProjectId.value, { force: true, isInitialLoading: true })
    }
}

const bulkArchiveDialog = ref({ open: false, preset: '7d' })
const toast = useToast()

// Computed rather than inline call in the template, so we don't rebuild
// the object on every render and we keep a stable reference.
const currentBulkArchiveScope = computed(() => {
    if (!isAllProjectsMode.value) {
        return { type: 'project', id: projectId.value }
    }
    if (activeWorkspaceId.value) {
        return { type: 'workspace', id: activeWorkspaceId.value }
    }
    return { type: 'all', id: null }
})

function openBulkArchiveDialog(preset) {
    bulkArchiveDialog.value = { open: true, preset }
}

function handleBulkArchived({ count }) {
    toast.success(`Archived ${count} session${count > 1 ? 's' : ''}.`)
}

// Handle session options dropdown selection
function handleSessionOptionsSelect(event) {
    const item = event.detail.item
    const value = item.value
    if (value && value.startsWith('archive-older-')) {
        const preset = value.slice('archive-older-'.length)
        openBulkArchiveDialog(preset)
        return
    }
    if (value === 'show-archived') {
        settingsStore.setShowArchivedSessions(item.checked)
    } else if (value === 'compact-view') {
        settingsStore.setCompactSessionList(item.checked)
    } else if (value === 'show-active') {
        settingsStore.setShowActiveAcrossFilters(item.checked)
    } else if (value === 'multi-select') {
        // The open session (already visually marked) seeds the Shift+click anchor.
        if (item.checked) selectionStore.enter(sessionId.value)
        else selectionStore.exit()
    }
}

// Handle project/workspace selection from the dropdown selector
function handleSelectorSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return

    if (value.startsWith('worktrees-toggle:')) {
        // Expand/collapse a project's worktrees inline — keep the dropdown open.
        const toggleItem = event.detail?.item
        toggleWorktrees(value.slice('worktrees-toggle:'.length))
        event.preventDefault()
        // Adding/removing the worktree rows changes the dropdown's slotted items,
        // which makes Web Awesome reset the active item to index 0 ("All Projects")
        // via its slotchange handler — while DOM focus stays on this toggle row. That
        // desyncs arrow-key navigation and Enter. Re-assert the active row on the
        // toggle once the re-render and WA's reset have settled.
        restoreSelectorActiveItem(toggleItem)
        return
    }

    if (value === ALL_PROJECTS_ID) {
        if (isAllProjectsMode.value && !activeWorkspaceId.value && sessionId.value) {
            router.push({ name: 'projects-all', query: {} })
        } else if (sessionId.value && projectId.value) {
            // Map single-project route names to all-projects equivalents
            let targetName = route.name
            if (!targetName.startsWith('projects-')) {
                targetName = 'projects-' + targetName
            }
            // Use workspace: '' to explicitly signal "no workspace" — the navigation guard
            // won't re-propagate because '' !== undefined. afterEach cleans the URL.
            router.push({ name: targetName, params: route.params, query: { workspace: '' } })
        } else {
            router.push({ name: 'projects-all', query: {} })
        }
    } else if (value.startsWith('workspace:')) {
        const wsId = value.slice('workspace:'.length)
        if (activeWorkspaceId.value === wsId && sessionId.value) {
            router.push({ name: 'projects-all', query: { workspace: wsId } })
        } else if (sessionId.value && projectId.value) {
            // A worktree of a member counts as part of the workspace, so keep
            // the user on the current session when its project belongs to it.
            if (workspacesStore.workspaceContainsProject(wsId, projectId.value)) {
                router.push({ name: 'projects-session', params: { projectId: projectId.value, sessionId: sessionId.value }, query: { workspace: wsId } })
            } else {
                router.push({ name: 'projects-all', query: { workspace: wsId } })
            }
        } else {
            router.push({ name: 'projects-all', query: { workspace: wsId } })
        }
    } else if (value === '__manage_workspaces__') {
        manageWorkspacesDialogRef.value?.open()
    } else if (value === 'ws-all') {
        router.push({ name: 'projects-all', query: { workspace: activeWorkspaceId.value } })
    } else {
        // Regular project selection — navigation guard handles workspace propagation
        router.push({ name: 'project', params: { projectId: value } })
    }
}

// Handle session selection (toggle: clicking already-selected session deselects it)
function handleSessionSelect(session) {
    if (session.id === sessionId.value) {
        // Deselect: navigate back to project root (keep workspace if active)
        if (isAllProjectsMode.value) {
            router.push({ name: 'projects-all', query: activeWorkspaceId.value ? { workspace: activeWorkspaceId.value } : {} })
        } else {
            router.push({ name: 'project', params: { projectId: projectId.value } })
        }
    } else if (isAllProjectsMode.value) {
        // Preserve the active workspace explicitly so the router guard does not
        // drop it when the destination project is outside the workspace (e.g.
        // clicking a cross-filter pinned/active session owned by another project).
        // This keeps the sidebar on its current workspace context — same
        // behavior as the deep-linked "extra" session.
        router.push({
            name: 'projects-session',
            params: { projectId: session.project_id, sessionId: session.id },
            query: activeWorkspaceId.value ? { workspace: activeWorkspaceId.value } : {},
        })
    } else {
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: session.id },
            query: activeWorkspaceId.value ? { workspace: activeWorkspaceId.value } : {},
        })
    }
}

/**
 * Handle files/text dropped on a session list item via drag-hover.
 * Navigates to the session and stores the drop data for SessionView to process.
 */
function handleDropOnSession({ session, files, text }) {
    // Session is already active (onActivate navigated to it before the drop).
    // Just store the drop data — SessionView will pick it up.
    pendingDropData.value = { sessionId: session.id, files, text }
}

// Navigate back to home
function handleBackHome() {
    router.push({ name: 'home' })
}

// Create a new draft session and navigate to it.
// In single project mode: uses the current projectId when targetProjectId is
// omitted; when targetProjectId is set the draft is created there but the
// sidebar filter stays on the current project (the draft shows up via the
// cross-filter fallback with its project color dot).
// In all projects mode: requires an explicit targetProjectId, used both for
// the draft and for the URL's canonical projectId (the sidebar stays on
// "All Projects" because the route name carries that mode).
async function handleNewSession(targetProjectId = null) {
    const projectIdToUse = targetProjectId || projectId.value
    if (!projectIdToUse) return

    // Trust gate: settle the project's trust before starting a session in it.
    // Its result is authoritative for the draft seed (the store may not have
    // caught up with a backend seed broadcast yet).
    const gate = await ensureProjectTrust(projectIdToUse)
    if (!gate) return

    const newSessionId = store.createDraftSession(projectIdToUse, gate.state)

    // `query: route.query` preserves ?workspace=… (and any other query params)
    // across the navigation. The router guard would normally drop workspace
    // when navigating to a project outside it, but we set workspace explicitly
    // so the guard short-circuits and our value wins.
    if (isAllProjectsMode.value) {
        router.push({
            name: 'projects-session',
            params: { projectId: projectIdToUse, sessionId: newSessionId },
            query: route.query,
        })
    } else {
        // Single-project mode: URL's projectId stays on the current filter so
        // the sidebar does not switch. When targetProjectId is not set,
        // projectId.value === projectIdToUse so the URL matches the draft
        // naturally; when it is set, they differ (cross-filter draft).
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: newSessionId },
            query: route.query,
        })
    }
}

// Create project from the "New session" dropdown
const createProjectDialogRef = ref(null)

// Workspace management dialog
const manageWorkspacesDialogRef = ref(null)

// ----- Project selector per-row "…" action menus -----
// Each project / workspace row in the selector carries a nested wa-dropdown
// (its actions menu). Web Awesome auto-closes other open dropdowns when a new one
// opens, which would also tear down the selector itself. We veto the selector's
// own wa-hide while one of those row menus is actually open.
//
// "Actually open" is read from the live DOM (the nested wa-dropdown's `open`
// attribute, reflected by Web Awesome) rather than tracked with a counter: a row
// can be unmounted in the middle of its close — e.g. archiving a project flips
// `archived`, which filters its row out before the row menu's wa-after-hide can
// fire — so any +1/-1 bookkeeping leaks and wedges the selector permanently open.
// A DOM query can't desync that way.
const selectorEl = ref(null)
// Set while closeSelector() forces the selector shut, so onSelectorHide lets that
// close through. Needed because closeSelector() runs from a row "…" menu's wa-select
// handler (edit / new session), at which point Web Awesome has NOT yet flipped that
// row menu's own `open` to false — so the DOM still shows an open child dropdown and
// the veto below would otherwise keep the selector wedged open.
let forceClosingSelector = false
// Edit dialog dedicated to the sidebar selector (distinct from the create one).
const sidebarProjectEditRef = ref(null)
const sidebarEditingProject = ref(null)

function onSelectorHide(event) {
    // Veto only the selector's own close, and only while a row "…" menu is open.
    // A child menu's wa-hide (different target) must pass through untouched.
    if (event.target !== selectorEl.value) return
    if (forceClosingSelector) {
        // Explicit programmatic close (closeSelector) — never veto it.
        forceClosingSelector = false
        return
    }
    if (selectorEl.value.querySelector('wa-dropdown[open]')) {
        event.preventDefault()
    }
}

// Force the selector closed (used right before opening a dialog over it, or when a
// row menu picks an action that should dismiss the selector — edit / new session).
function closeSelector() {
    const el = selectorEl.value
    if (el && el.open) {
        forceClosingSelector = true
        el.open = false
    }
}

// Re-assert which top-level selector row is "active" (drives WA's arrow-key /
// Enter navigation) and refocus it. Used after an in-place toggle that mutates the
// slotted item list (worktrees expand/collapse), since WA's slotchange handler
// resets the active item to index 0. Runs after Vue's DOM patch (nextTick) and
// after WA's async reset (an extra rAF, since WA awaits each item's updateComplete
// before reassigning), so our assignment wins.
function restoreSelectorActiveItem(item) {
    if (!item) return
    nextTick(() => {
        requestAnimationFrame(() => {
            const el = selectorEl.value
            if (!el || !item.isConnected) return
            el.querySelectorAll(':scope > wa-dropdown-item').forEach((row) => {
                row.active = row === item
            })
            item.focus({ preventScroll: true })
        })
    })
}

// Tab on a selector row opens that row's "…" actions menu and moves focus into it,
// instead of Web Awesome's default (Tab closes the whole dropdown). Capture phase so
// we run before WA's document-level keydown handler. Shift+Tab keeps WA's default;
// rows without a menu (All Projects, the "Worktrees (N)" header) just swallow Tab so
// the selector stays open. We never intervene when focus is already inside a row
// menu (the focused item is then a child of a nested dropdown, not of the selector).
function onSelectorKeydown(event) {
    if (event.key !== 'Tab' || event.shiftKey) return
    const row = event.target?.closest?.('wa-dropdown-item')
    if (!row || row.parentElement !== selectorEl.value) return
    event.preventDefault()
    event.stopPropagation()
    const rowMenu = row.querySelector('.row-menu wa-dropdown')
    // Opening the nested dropdown makes WA focus its first item for us (showMenu).
    if (rowMenu) rowMenu.open = true
}

// Provided to ProjectSelectorRow (named / tree / worktree rows) so they can open
// the shared edit dialog from their "…" menu without per-level event forwarding.
function openProjectEditDialog(projectId) {
    sidebarEditingProject.value = store.getProject(projectId)
    closeSelector()
    sidebarProjectEditRef.value?.open()
}
provide('openProjectEditDialog', openProjectEditDialog)

// Provided to ProjectSelectorRow so a row's "…" menu can spin up a draft session
// directly in that project — same action as the bottom "New session" button.
// Only offered for non-stale projects (the row hides the entry otherwise).
function createSessionInProject(projectId) {
    closeSelector()
    handleNewSession(projectId)
}
provide('createSessionInProject', createSessionInProject)

// Workspace row "…" menu actions (workspace rows are inline in this template).
function onWorkspaceRowMenuSelect(event, ws) {
    const value = event.detail?.item?.value
    if (value === 'edit') {
        closeSelector()
        manageWorkspacesDialogRef.value?.openForWorkspace(ws.id)
    } else if (value === 'archive') {
        workspacesStore.updateWorkspace(ws.id, { archived: true })
    } else if (value === 'unarchive') {
        workspacesStore.updateWorkspace(ws.id, { archived: false })
    }
}

function handleNewSessionSelect(e) {
    const value = e.detail.item.value
    if (value.startsWith('worktrees-toggle:')) {
        // Expand/collapse a project's worktrees inline — keep the dropdown open.
        toggleNewSessionWorktrees(value.slice('worktrees-toggle:'.length))
        e.preventDefault()
        return
    }
    if (value === '__new_project__') {
        createProjectDialogRef.value?.open()
    } else {
        handleNewSession(value)
    }
}

function handleProjectCreated(project) {
    handleNewSession(project.id)
}

// ----- New worktree (button on git-project rows of the "New session" dropdowns) -----
const worktreeDialogRef = ref(null)
const newSessionSplitDropdownRef = ref(null)
const newSessionAllDropdownRef = ref(null)

function openWorktreeDialog(project) {
    // The button click is stopPropagation'd (no row selection), so the hosting
    // dropdown stays open — close whichever one is visible before the dialog.
    for (const dd of [newSessionSplitDropdownRef.value, newSessionAllDropdownRef.value]) {
        if (dd) dd.open = false
    }
    worktreeDialogRef.value?.open(project)
}

function handleWorktreeResolved(project) {
    handleNewSession(project.id)
}

// Sidebar state persistence
const SIDEBAR_STORAGE_KEY = 'twicc-sidebar-state'
const DEFAULT_SIDEBAR_WIDTH = 300
// Sidebar collapse threshold in pixels
const SIDEBAR_COLLAPSE_THRESHOLD = 120
// Mobile breakpoint (must match CSS media query)
const MOBILE_BREAKPOINT = 640

// Load sidebar state from localStorage
function loadSidebarState() {
    try {
        const stored = localStorage.getItem(SIDEBAR_STORAGE_KEY)
        if (stored) {
            const parsed = JSON.parse(stored)
            return {
                open: typeof parsed.open === 'boolean' ? parsed.open : true,
                width: typeof parsed.width === 'number' && parsed.width > 0 ? parsed.width : DEFAULT_SIDEBAR_WIDTH,
            }
        }
    } catch (e) {
        console.warn('Failed to load sidebar state from localStorage:', e)
    }
    return { open: true, width: DEFAULT_SIDEBAR_WIDTH }
}

// Save sidebar state to localStorage
function saveSidebarState(state) {
    try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify(state))
    } catch (e) {
        console.warn('Failed to save sidebar state to localStorage:', e)
    }
}

// Current sidebar state (non-reactive, applied once at mount)
const sidebarState = loadSidebarState()

// Check if we're on mobile (for initial sidebar state)
const isMobile = () => window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches

// Initial checkbox state:
// - On mobile: checked = open, so check when no session
// - On desktop: checked = closed, so check if sidebar was closed (inverted logic)
const initialSidebarChecked = computed(() => {
    if (typeof window === 'undefined') return false
    if (isMobile()) {
        return !sessionId.value
    }
    // Desktop: checkbox checked means closed, so invert the stored "open" state
    return !sidebarState.open
})

// Track all route changes for MRU (Most Recently Used) navigation.
// Stores the full path (including sub-routes like /files, /git, /terminal)
// so we can restore the exact view when navigating back after archiving.
watch(() => route.fullPath, (newPath) => {
    store.touchMruPath(newPath, sessionId.value)
}, { immediate: true })

// Navigate to previous MRU path when the current session gets archived.
// Uses a computed to reactively track the archived state of the current session,
// so this works regardless of where the archive action was triggered
// (session list menu, session header button, etc.).
// Only reacts when a session transitions from non-archived to archived while
// we are viewing it, not when navigating to a session that was already archived
// (e.g. from search results).
//
// The key challenge: when navigating to a session in a different project, the
// session data isn't in the store yet. The computed returns null (not loaded),
// then when loadSessions() completes, it flips to true. We must not treat this
// as an "archive action" — we use a three-state computed (null/false/true) and
// a flag that tracks whether we've ever seen the session as non-archived.
const currentSessionArchived = computed(() => {
    if (!sessionId.value) return null
    const session = store.sessions[sessionId.value]
    if (!session) return null  // session not loaded yet
    return session.archived ?? false
})

// Tracks whether we've confirmed that the current session was non-archived
// after its data loaded. Only redirect when this is true (meaning the session
// was actively archived while we were viewing it).
let sessionConfirmedNonArchived = false

watch(sessionId, () => {
    // When navigating between two non-archived sessions in the same project,
    // currentSessionArchived stays false (no value change), so its watcher
    // won't fire to re-confirm the flag. We must check immediately here.
    const archived = currentSessionArchived.value
    sessionConfirmedNonArchived = archived === false
})

watch(currentSessionArchived, (archived) => {
    if (archived === null) return  // session not loaded yet, ignore
    if (!archived) {
        // Session is loaded and not archived — record this so we can
        // detect a future false→true transition as an archive action.
        sessionConfirmedNonArchived = true
        return
    }
    // archived is true — only redirect if we previously saw it as non-archived
    if (!sessionConfirmedNonArchived) return
    if (showArchivedSessions.value) return  // session stays visible, no need to navigate

    const nextPath = store.getNextMruPath(sessionId.value)
    if (nextPath) {
        router.push(nextPath)
    }
}, { immediate: true })

// Redirect away from workspace if it becomes non-activable, archived (when hidden), or deleted
watch(
    [activeWorkspaceId, () => workspacesStore.workspaces, () => settingsStore.isShowArchivedWorkspaces, () => settingsStore.isShowArchivedProjects],
    () => {
        if (!activeWorkspaceId.value) return
        const ws = workspacesStore.getWorkspaceById(activeWorkspaceId.value)
        const shouldClear = (
            !ws ||
            !workspacesStore.isActivable(ws.id) ||
            (ws.archived && !settingsStore.isShowArchivedWorkspaces)
        )
        if (shouldClear) {
            router.replace({ name: 'projects-all', query: {} })
        }
    }
)

// On mobile, close sidebar when session changes
watch(sessionId, (newSessionId) => {
    if (!newSessionId) return
    // Only on mobile
    if (!isMobile()) return

    const checkbox = document.getElementById('sidebar-toggle-state')
    if (checkbox) {
        checkbox.checked = false
    }
    updateSidebarClosedClass(true)
})

// Reset sidebar to default width on divider double-click.
// wa-split-panel's drag handler calls preventDefault() on mousedown, which prevents
// native dblclick events from firing. We detect double-clicks manually by tracking
// rapid consecutive pointerdown events on the split panel host element, filtering
// only those whose composedPath includes the shadow DOM divider.
function resetSidebarToDefault() {
    if (isMobile()) return
    const splitPanel = document.querySelector('.project-view')
    if (splitPanel) {
        ignoringReposition = true
        splitPanel.positionInPixels = DEFAULT_SIDEBAR_WIDTH
        requestAnimationFrame(() => {
            ignoringReposition = false
        })
        sidebarState.width = DEFAULT_SIDEBAR_WIDTH
        lastKnownPosition = DEFAULT_SIDEBAR_WIDTH
        saveSidebarState({ open: true, width: DEFAULT_SIDEBAR_WIDTH })
        updateSidebarClosedClass(false)
    }
}

const DOUBLE_CLICK_DELAY = 400 // ms
let lastDividerPointerDown = 0

function handleSplitPanelPointerDown(event) {
    // Only react to pointerdown events that originate from the divider
    // (check composedPath to see through shadow DOM boundary)
    const splitPanel = event.currentTarget
    const path = event.composedPath()
    if (!path.includes(splitPanel.divider)) return

    const now = Date.now()
    if (now - lastDividerPointerDown < DOUBLE_CLICK_DELAY) {
        lastDividerPointerDown = 0
        resetSidebarToDefault()
    } else {
        lastDividerPointerDown = now
    }
}

// Apply stored sidebar width and body class once on mount
onMounted(() => {
    const splitPanel = document.querySelector('.project-view')
    if (splitPanel) {
        if (sidebarState.width !== DEFAULT_SIDEBAR_WIDTH) {
            splitPanel.positionInPixels = sidebarState.width
        }
        lastKnownPosition = sidebarState.open ? sidebarState.width : 0
        // Listen for pointerdown on the host element (capture phase) to detect
        // double-clicks on the divider. Native dblclick is blocked by the drag handler.
        splitPanel.addEventListener('pointerdown', handleSplitPanelPointerDown, true)
    }
    // Set initial sidebar-closed class on body
    if (isMobile()) {
        updateSidebarClosedClass(!!sessionId.value)
    } else {
        updateSidebarClosedClass(!sidebarState.open)
    }

    // Register contextual commands in the command palette
    registerCommands([
        {
            id: 'ui.toggle-sidebar',
            label: 'Toggle Sidebar',
            icon: 'table-columns',
            category: 'ui',
            action: () => {
                const checkbox = document.getElementById('sidebar-toggle-state')
                if (checkbox) {
                    checkbox.checked = !checkbox.checked
                    checkbox.dispatchEvent(new Event('change'))
                }
            },
        },
        {
            id: 'ui.focus-search',
            label: 'Focus Session Filter',
            icon: 'magnifying-glass',
            category: 'ui',
            action: () => {
                searchInputRef.value?.focus()
            },
        },
        {
            id: 'ui.focus-project-selector',
            label: 'Focus Project Selector',
            icon: 'folder-tree',
            category: 'ui',
            // Open the sidebar's project/workspace switcher. On desktop, expand
            // the sidebar first if collapsed so the trigger anchors the menu;
            // opening the dropdown moves focus into it and enables typeahead.
            action: () => {
                const checkbox = document.getElementById('sidebar-toggle-state')
                if (!isMobile() && checkbox?.checked) {
                    checkbox.checked = false
                    checkbox.dispatchEvent(new Event('change'))
                }
                nextTick(() => { if (selectorEl.value) selectorEl.value.open = true })
            },
        },
    ])

    // Listen for custom events to open dialogs (triggered by command palette)
    window.addEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.addEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.addEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.addEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
})

function openNewProjectDialog() {
    createProjectDialogRef.value?.open()
}
function openNewWorkspaceDialog() {
    manageWorkspacesDialogRef.value?.openNew()
}
function openManageWorkspacesDialog() {
    manageWorkspacesDialogRef.value?.open()
}
function openEditWorkspaceDialog(e) {
    manageWorkspacesDialogRef.value?.openForWorkspace(e.detail?.workspaceId)
}

onBeforeUnmount(() => {
    unregisterCommands(['ui.toggle-sidebar', 'ui.focus-search', 'ui.focus-project-selector'])
    window.removeEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.removeEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.removeEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.removeEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
})

// Guard flag to ignore reposition events triggered by width restore after auto-collapse
let ignoringReposition = false
// Track the last known position to detect spurious reposition events (e.g. from
// KeepAlive reactivation or layout recalculations) that jump from a normal width
// to near-zero in a single step — no human drag can do that.
let lastKnownPosition = DEFAULT_SIDEBAR_WIDTH

// Handle split panel reposition: auto-collapse when dragged to threshold, and persist width.
// Ignored on mobile where the split panel is not used (sidebar is an overlay).
function handleSplitReposition(event) {
    // Ignore events bubbling from nested wa-split-panels (e.g. FilesPanel's tree/content splitter).
    // Only handle events from our own split panel.
    if (event.target !== event.currentTarget) return

    if (isMobile()) return
    if (ignoringReposition) return

    const checkbox = document.getElementById('sidebar-toggle-state')
    if (!checkbox) return

    // Desktop: checkbox checked = sidebar closed. Ignore reposition events when closed —
    // the sidebar is hidden via CSS (grid-template-columns: 0), so any events are
    // spurious layout recalculations (e.g. initial mount, KeepAlive reactivation).
    if (checkbox.checked) return

    const newWidth = event.target.positionInPixels

    // Ignore null/NaN positions emitted during KeepAlive transitions.
    // wa-split-panel fires wa-reposition with positionInPixels = null when
    // the component is deactivated/reactivated by KeepAlive.
    if (newWidth == null || Number.isNaN(newWidth)) return

    if (newWidth <= SIDEBAR_COLLAPSE_THRESHOLD) {
        // Ignore spurious collapse: if the sidebar was at a normal width and jumped
        // to near-zero in one step, this is a layout recalculation (e.g. KeepAlive
        // reactivation), not a user drag. Restore the stored width instead.
        if (lastKnownPosition > SIDEBAR_COLLAPSE_THRESHOLD * 2) {
            ignoringReposition = true
            requestAnimationFrame(() => {
                event.target.positionInPixels = sidebarState.width
                requestAnimationFrame(() => {
                    ignoringReposition = false
                    lastKnownPosition = sidebarState.width
                })
            })
            return
        }
        // Auto-collapse: mark as closed, reset width to stored value
        lastKnownPosition = 0
        checkbox.checked = true
        saveSidebarState({ open: false, width: sidebarState.width })
        updateSidebarClosedClass(true)
        // Restore width, ignoring the resulting reposition event
        ignoringReposition = true
        requestAnimationFrame(() => {
            event.target.positionInPixels = sidebarState.width
            requestAnimationFrame(() => {
                ignoringReposition = false
            })
        })
    } else {
        // Normal resize: update stored width
        lastKnownPosition = newWidth
        sidebarState.width = newWidth
        saveSidebarState({ open: true, width: newWidth })
        updateSidebarClosedClass(false)
    }
}

// Handle sidebar toggle (called when checkbox changes)
function handleSidebarToggle(event) {
    const checked = event.target.checked
    if (isMobile()) {
        // Mobile: checked = open
        updateSidebarClosedClass(!checked)
    } else {
        // Desktop: checked = closed
        const isOpen = !checked
        lastKnownPosition = isOpen ? sidebarState.width : 0
        saveSidebarState({ open: isOpen, width: sidebarState.width })
        updateSidebarClosedClass(checked)
    }
}

// Toggle body class to indicate sidebar is closed.
// Used by child components (e.g. MessageInput) to adjust layout
// when the sidebar toggle button overlaps their content.
function updateSidebarClosedClass(closed) {
    document.body.classList.toggle('sidebar-closed', closed)
}
</script>

<template>
    <div class="project-view-wrapper">
        <!-- Hidden checkbox for pure CSS sidebar toggle -->
        <input type="checkbox" id="sidebar-toggle-state" class="sidebar-toggle-checkbox" :checked="initialSidebarChecked" @change="handleSidebarToggle"/>

        <wa-split-panel
            class="project-view"
            :position-in-pixels="DEFAULT_SIDEBAR_WIDTH"
            primary="start"
            snap="125px 200px 300px 400px"
            snap-threshold="30"
            @wa-reposition="handleSplitReposition"
        >
            <!-- Divider handle for touch devices -->
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- Sidebar -->
        <aside slot="start" class="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-header-row">
                    <wa-button id="back-button" class="back-button" variant="brand" appearance="outlined" size="small" @click="handleBackHome">
                        <wa-icon name="arrow-left"></wa-icon>
                    </wa-button>
                    <AppTooltip for="back-button">Back to projects list</AppTooltip>
                    <wa-dropdown
                        ref="selectorEl"
                        id="project-selector"
                        class="project-selector"
                        @keydown.capture="onSelectorKeydown"
                        @wa-select="handleSelectorSelect"
                        @wa-hide="onSelectorHide"
                    >
                        <wa-button
                            slot="trigger"
                            variant="brand"
                            appearance="outlined"
                            size="small"
                            class="project-selector-trigger"
                        >
                            <span v-if="!isAllProjectsMode" class="selected-project-dot" :style="selectedProjectColor ? { '--dot-color': selectedProjectColor } : null"></span>
                            <span v-if="isWorkspaceMode" class="project-selector-label"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWorkspace?.name }}</span>
                            <span v-else-if="isAllProjectsMode" class="project-selector-label">All Projects</span>
                            <span v-else-if="isCurrentProjectWorktree" class="project-selector-label"><WorktreeBadge :project-id="projectId" :dot="false" gap="var(--wa-space-2xs)" /></span>
                            <span v-else class="project-selector-label" :title="selectedProjectTitle">{{ selectedProjectLabel }}</span>

                            <wa-icon slot="end" name="chevron-down" class="project-selector-caret"></wa-icon>
                        </wa-button>

                        <!-- When no workspace is active -->
                        <template v-if="!activeWorkspaceId">
                            <wa-dropdown-item :value="ALL_PROJECTS_ID">
                                <wa-icon slot="icon" name="check" :style="{ visibility: isAllProjectsMode ? 'visible' : 'hidden' }"></wa-icon>
                                All Projects
                            </wa-dropdown-item>

                            <wa-divider></wa-divider>
                            <template v-if="workspacesStore.getSelectableWorkspaces.length">
                                <wa-dropdown-item disabled class="section-header-item">
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    Workspaces
                                </wa-dropdown-item>
                                <wa-dropdown-item
                                    v-for="ws in workspacesStore.getSelectableWorkspaces"
                                    :key="ws.id"
                                    :value="'workspace:' + ws.id"
                                    :class="{ 'selector-row-archived': ws.archived }"
                                >
                                    <wa-icon slot="icon" name="layer-group" :style="ws.color ? { color: ws.color } : null"></wa-icon>
                                    <span class="selector-item-content">
                                        {{ ws.name }}
                                        <span class="selector-item-indicators">
                                            <CodeCommentsIndicator :project-ids="workspacesStore.getVisibleProjectIds(ws.id)" />
                                            <AggregatedProcessIndicator :project-ids="workspacesStore.getVisibleProjectIds(ws.id)" size="small" />
                                        </span>
                                        <span class="row-menu" @click.stop>
                                            <wa-dropdown placement="bottom-end" @wa-select.stop="onWorkspaceRowMenuSelect($event, ws)">
                                                <wa-button slot="trigger" appearance="plain" size="small" class="row-menu-trigger">
                                                    <wa-icon name="ellipsis" label="Workspace actions"></wa-icon>
                                                </wa-button>
                                                <wa-dropdown-item value="edit">
                                                    <wa-icon slot="icon" name="pencil"></wa-icon>
                                                    Edit
                                                </wa-dropdown-item>
                                                <wa-dropdown-item v-if="!ws.archived" value="archive">
                                                    <wa-icon slot="icon" name="box-archive"></wa-icon>
                                                    Archive
                                                </wa-dropdown-item>
                                                <wa-dropdown-item v-else value="unarchive">
                                                    <wa-icon slot="icon" name="box-open"></wa-icon>
                                                    Unarchive
                                                </wa-dropdown-item>
                                            </wa-dropdown>
                                        </span>
                                    </span>
                                </wa-dropdown-item>
                            </template>
                            <wa-dropdown-item value="__manage_workspaces__">
                                <wa-icon slot="icon" name="gear"></wa-icon>
                                Manage workspaces...
                            </wa-dropdown-item>

                            <!-- Named projects -->
                            <wa-divider v-if="selectorNamedProjects.length"></wa-divider>
                            <template v-for="p in selectorNamedProjects" :key="p.id">
                                <ProjectSelectorRow :project-id="p.id" :current-project-id="projectId" :is-all-projects-mode="isAllProjectsMode" />
                                <WorktreeSelectorRows
                                    :parent-id="p.id"
                                    :worktrees="visibleWorktreesOf(p.id)"
                                    :expanded="isWorktreesExpanded(p.id)"
                                    :base-depth="0"
                                    :current-project-id="projectId"
                                    :is-all-projects-mode="isAllProjectsMode"
                                />
                            </template>

                            <!-- Unnamed projects (directory tree) -->
                            <wa-divider v-if="selectorFlatTree.length"></wa-divider>
                            <template v-for="item in selectorFlatTree" :key="item.key">
                                <wa-dropdown-item
                                    v-if="item.isFolder"
                                    disabled
                                    class="tree-folder-dropdown-item"
                                >
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        {{ item.segment }}
                                    </span>
                                </wa-dropdown-item>
                                <template v-else>
                                    <ProjectSelectorRow :project-id="item.project.id" :depth="item.depth" :current-project-id="projectId" :is-all-projects-mode="isAllProjectsMode" />
                                    <WorktreeSelectorRows
                                        :parent-id="item.project.id"
                                        :worktrees="visibleWorktreesOf(item.project.id)"
                                        :expanded="isWorktreesExpanded(item.project.id)"
                                        :base-depth="item.depth"
                                        :current-project-id="projectId"
                                        :is-all-projects-mode="isAllProjectsMode"
                                    />
                                </template>
                            </template>
                        </template>

                        <!-- When a workspace is active -->
                        <template v-else>
                            <wa-dropdown-item :value="ALL_PROJECTS_ID">
                                <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                All Projects
                            </wa-dropdown-item>

                            <wa-divider></wa-divider>
                            <wa-dropdown-item disabled class="section-header-item">
                                <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                Workspaces
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-for="ws in workspacesStore.getSelectableWorkspaces"
                                :key="ws.id"
                                :value="'workspace:' + ws.id"
                                :class="{ 'selector-row-archived': ws.archived }"
                            >
                                <wa-icon slot="icon" :name="ws.id === activeWorkspaceId ? 'check' : 'layer-group'" :style="ws.id !== activeWorkspaceId && ws.color ? { color: ws.color } : null"></wa-icon>
                                <span class="selector-item-content">
                                    {{ ws.name }}
                                    <span class="selector-item-indicators">
                                        <CodeCommentsIndicator :project-ids="workspacesStore.getVisibleProjectIds(ws.id)" />
                                        <AggregatedProcessIndicator :project-ids="workspacesStore.getVisibleProjectIds(ws.id)" size="small" />
                                    </span>
                                    <span class="row-menu" @click.stop>
                                        <wa-dropdown placement="bottom-end" @wa-select.stop="onWorkspaceRowMenuSelect($event, ws)">
                                            <wa-button slot="trigger" appearance="plain" size="small" class="row-menu-trigger">
                                                <wa-icon name="ellipsis" label="Workspace actions"></wa-icon>
                                            </wa-button>
                                            <wa-dropdown-item value="edit">
                                                <wa-icon slot="icon" name="pencil"></wa-icon>
                                                Edit
                                            </wa-dropdown-item>
                                            <wa-dropdown-item v-if="!ws.archived" value="archive">
                                                <wa-icon slot="icon" name="box-archive"></wa-icon>
                                                Archive
                                            </wa-dropdown-item>
                                            <wa-dropdown-item v-else value="unarchive">
                                                <wa-icon slot="icon" name="box-open"></wa-icon>
                                                Unarchive
                                            </wa-dropdown-item>
                                        </wa-dropdown>
                                    </span>
                                </span>
                            </wa-dropdown-item>
                            <wa-dropdown-item value="__manage_workspaces__">
                                <wa-icon slot="icon" name="gear"></wa-icon>
                                Manage workspaces...
                            </wa-dropdown-item>

                            <wa-divider></wa-divider>
                            <!-- Workspace projects sub-section -->
                            <wa-dropdown-item disabled class="section-header-item">
                                <wa-icon slot="icon" name="layer-group" :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon>
                                {{ activeWorkspace?.name }} projects
                            </wa-dropdown-item>
                            <wa-dropdown-item value="ws-all">
                                <wa-icon slot="icon" name="check" :style="{ visibility: isAllProjectsMode ? 'visible' : 'hidden' }"></wa-icon>
                                All projects
                            </wa-dropdown-item>
                            <template v-for="pid in workspaceSelectorProjectIds" :key="pid">
                                <ProjectSelectorRow :project-id="pid" :current-project-id="projectId" :is-all-projects-mode="isAllProjectsMode" />
                                <WorktreeSelectorRows
                                    :parent-id="pid"
                                    :worktrees="visibleWorktreesOf(pid)"
                                    :expanded="isWorktreesExpanded(pid)"
                                    :base-depth="0"
                                    :current-project-id="projectId"
                                    :is-all-projects-mode="isAllProjectsMode"
                                />
                            </template>

                            <!-- Other projects (not in workspace) -->
                            <template v-if="otherNamedProjects.length || otherFlatTree.length">
                                <wa-divider></wa-divider>
                                <wa-dropdown-item disabled class="section-header-item">
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    Other projects
                                </wa-dropdown-item>
                            </template>
                            <!-- Other named projects -->
                            <template v-for="p in otherNamedProjects" :key="p.id">
                                <ProjectSelectorRow :project-id="p.id" :current-project-id="projectId" :is-all-projects-mode="isAllProjectsMode" />
                                <WorktreeSelectorRows
                                    :parent-id="p.id"
                                    :worktrees="visibleWorktreesOf(p.id)"
                                    :expanded="isWorktreesExpanded(p.id)"
                                    :base-depth="0"
                                    :current-project-id="projectId"
                                    :is-all-projects-mode="isAllProjectsMode"
                                />
                            </template>
                            <!-- Other unnamed projects (directory tree) -->
                            <wa-divider v-if="otherNamedProjects.length && otherFlatTree.length"></wa-divider>
                            <template v-for="item in otherFlatTree" :key="item.key">
                                <wa-dropdown-item
                                    v-if="item.isFolder"
                                    disabled
                                    class="tree-folder-dropdown-item"
                                >
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        {{ item.segment }}
                                    </span>
                                </wa-dropdown-item>
                                <template v-else>
                                    <ProjectSelectorRow :project-id="item.project.id" :depth="item.depth" :current-project-id="projectId" :is-all-projects-mode="isAllProjectsMode" />
                                    <WorktreeSelectorRows
                                        :parent-id="item.project.id"
                                        :worktrees="visibleWorktreesOf(item.project.id)"
                                        :expanded="isWorktreesExpanded(item.project.id)"
                                        :base-depth="item.depth"
                                        :current-project-id="projectId"
                                        :is-all-projects-mode="isAllProjectsMode"
                                    />
                                </template>
                            </template>
                        </template>
                    </wa-dropdown>
                </div>

                <div class="sidebar-header-row">
                    <!-- Session list options dropdown -->
                    <wa-dropdown
                        placement="bottom-end"
                        class="session-options-dropdown"
                        @wa-select="handleSessionOptionsSelect"
                    >
                        <wa-button
                            id="session-options-button"
                            slot="trigger"
                            variant="neutral"
                            appearance="filled-outlined"
                            size="small"
                        >
                            <wa-icon name="sliders"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item
                            type="checkbox"
                            value="show-archived"
                            :checked="showArchivedSessions"
                        >
                            Show archived sessions
                        </wa-dropdown-item>
                        <wa-dropdown-item value="archive-older">
                            <wa-icon slot="icon" name="box-archive"></wa-icon>
                            {{ trimmedSearchQuery ? 'Archive filtered sessions older than…' : 'Archive sessions older than…' }}
                            <wa-dropdown-item slot="submenu" value="archive-older-3d">3 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-7d">7 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-10d">10 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-20d">20 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-30d">30 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-2m">2 months</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-3m">3 months</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-6m">6 months</wa-dropdown-item>
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            type="checkbox"
                            value="compact-view"
                            :checked="compactView"
                        >
                            Compact view
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            type="checkbox"
                            value="multi-select"
                            :checked="selectionStore.active"
                        >
                            Multi-select mode
                        </wa-dropdown-item>
                        <wa-divider></wa-divider>
                        <wa-dropdown-item
                            type="checkbox"
                            value="show-active"
                            :checked="showActiveAcrossFilters"
                        >
                            <div>Show active sessions across projects</div>
                            <div class="session-option-hint">All with a running process or unread content</div>
                        </wa-dropdown-item>
                    </wa-dropdown>
                    <AppTooltip for="session-options-button">Session list options</AppTooltip>

                    <!-- Search/filter input -->
                    <wa-input
                        ref="searchInputRef"
                        v-model="searchQuery"
                        placeholder="Filter sessions..."
                        size="small"
                        with-clear
                        class="session-search"
                        @keydown="handleSearchKeydown"
                    >
                        <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                    </wa-input>
                    <wa-button
                        id="search-advanced-button"
                        variant="neutral"
                        appearance="filled-outlined"
                        size="small"
                        class="search-advanced-button"
                        @click="openAdvancedSearch"
                    >
                        <wa-icon name="plus"></wa-icon>
                    </wa-button>
                    <AppTooltip for="search-advanced-button">Full-text search (Ctrl+Shift+F)</AppTooltip>
                </div>
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-sessions">
                <!-- Multi-select mode: in-flow action bar above the list -->
                <SessionSelectionBar
                    v-if="selectionStore.active"
                    :active-session-id="sessionId"
                    @deselect-session="handleSessionSelect"
                />

                <!-- Error state (only for initial load failure) -->
                <FetchErrorPanel
                    v-if="didSessionsFailToLoad && !areSessionsFetched"
                    :loading="isInitialLoading"
                    @retry="handleRetry"
                >
                    Failed to load sessions
                </FetchErrorPanel>

                <!-- Initial loading state (only before first fetch) -->
                <div v-else-if="isInitialLoading" class="sessions-loading">
                    <wa-spinner></wa-spinner>
                    <span>Loading...</span>
                </div>

                <!-- Normal content (shown once we have sessions, handles its own "load more" state) -->
                <SessionList
                    v-else
                    ref="sessionListRef"
                    :project-id="effectiveProjectId"
                    :session-id="sessionId"
                    :show-project-name="showProjectNameInList"
                    :search-query="searchQuery"
                    :show-archived="showArchivedSessions"
                    :show-archived-projects="showArchivedProjects"
                    :compact-view="compactView"
                    :show-active-across-filters="showActiveAcrossFilters"
                    @select="handleSessionSelect"
                    @drop-data="handleDropOnSession"
                    @focus-search="focusSearchInput"
                />

                <!-- Floating "New session" button -->
                <!-- In single project mode: split button (main action + dropdown for other projects) -->
                <wa-button-group
                    v-if="!isAllProjectsMode"
                    class="new-session-split-button"
                    label="New session actions"
                >
                    <!-- Main button: creates session in current project -->
                    <wa-button
                        id="new-session-button"
                        variant="brand"
                        appearance="accent"
                        size="small"
                        :disabled="isCurrentProjectStale"
                        @click="handleNewSession()"
                    >
                        <wa-icon name="plus"></wa-icon>
                        <span>New session</span>
                    </wa-button>

                    <!-- Dropdown arrow: choose a different project -->
                    <wa-dropdown
                        ref="newSessionSplitDropdownRef"
                        placement="top-end"
                        @wa-select="handleNewSessionSelect"
                    >
                        <wa-button
                            id="new-session-project-picker"
                            slot="trigger"
                            variant="brand"
                            appearance="accent"
                            size="small"
                        >
                            <wa-icon name="chevron-up" label="Choose another project"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item value="__new_project__">
                            <wa-icon slot="icon" name="plus"></wa-icon>
                            New project
                        </wa-dropdown-item>

                        <!-- Workspace projects first (when workspace active) -->
                        <template v-if="splitNamedProjects.prioritized.length || splitFlatTree.prioritized.length">
                            <wa-divider></wa-divider>
                            <wa-dropdown-item v-if="activeWsLabel" disabled class="section-header-item"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWsLabel }}</wa-dropdown-item>
                        </template>
                        <template v-for="p in splitNamedProjects.prioritized" :key="p.id">
                            <wa-dropdown-item :value="p.id" class="project-picker-row">
                                <ProjectBadge :project-id="p.id" />
                                <WorktreeButton v-if="p.git_root" slot="details" @create="openWorktreeDialog(p)" />
                            </wa-dropdown-item>
                            <WorktreePickerRows
                                :parent-id="p.id"
                                :worktrees="pickableWorktreesOf(p.id)"
                                :expanded="isNewSessionWorktreesExpanded(p.id)"
                                :base-depth="0"
                            />
                        </template>
                        <template v-for="item in splitFlatTree.prioritized" :key="'wsp-' + item.key">
                            <wa-dropdown-item
                                v-if="item.isFolder"
                                disabled
                                class="tree-folder-dropdown-item"
                            >
                                <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    {{ item.segment }}
                                </span>
                            </wa-dropdown-item>
                            <template v-else>
                                <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        <ProjectBadge :project-id="item.project.id" />
                                    </span>
                                    <WorktreeButton v-if="item.project.git_root" slot="details" @create="openWorktreeDialog(item.project)" />
                                </wa-dropdown-item>
                                <WorktreePickerRows
                                    :parent-id="item.project.id"
                                    :worktrees="pickableWorktreesOf(item.project.id)"
                                    :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                                    :base-depth="item.depth"
                                />
                            </template>
                        </template>

                        <!-- Other projects -->
                        <template v-if="activeWsLabel && (splitNamedProjects.others.length || splitFlatTree.others.length)">
                            <wa-divider></wa-divider>
                            <wa-dropdown-item disabled class="section-header-item">Other projects</wa-dropdown-item>
                        </template>
                        <wa-divider v-else-if="splitNamedProjects.others.length"></wa-divider>
                        <template v-for="p in splitNamedProjects.others" :key="p.id">
                            <wa-dropdown-item :value="p.id" class="project-picker-row">
                                <ProjectBadge :project-id="p.id" />
                                <WorktreeButton v-if="p.git_root" slot="details" @create="openWorktreeDialog(p)" />
                            </wa-dropdown-item>
                            <WorktreePickerRows
                                :parent-id="p.id"
                                :worktrees="pickableWorktreesOf(p.id)"
                                :expanded="isNewSessionWorktreesExpanded(p.id)"
                                :base-depth="0"
                            />
                        </template>

                        <!-- Other unnamed projects (flattened tree) -->
                        <wa-divider v-if="splitFlatTree.others.length"></wa-divider>
                        <template v-for="item in splitFlatTree.others" :key="item.key">
                            <wa-dropdown-item
                                v-if="item.isFolder"
                                disabled
                                class="tree-folder-dropdown-item"
                            >
                                <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    {{ item.segment }}
                                </span>
                            </wa-dropdown-item>
                            <template v-else>
                                <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        <ProjectBadge :project-id="item.project.id" />
                                    </span>
                                    <WorktreeButton v-if="item.project.git_root" slot="details" @create="openWorktreeDialog(item.project)" />
                                </wa-dropdown-item>
                                <WorktreePickerRows
                                    :parent-id="item.project.id"
                                    :worktrees="pickableWorktreesOf(item.project.id)"
                                    :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                                    :base-depth="item.depth"
                                />
                            </template>
                        </template>
                    </wa-dropdown>
                </wa-button-group>

                <template v-if="!isAllProjectsMode">
                    <AppTooltip for="new-session-button">Create a new session in this project</AppTooltip>
                    <AppTooltip for="new-session-project-picker">Choose a different project</AppTooltip>
                </template>

                <!-- In all projects mode: dropdown to choose project -->
                <wa-dropdown
                    v-if="isAllProjectsMode"
                    id="new-session-dropdown"
                    ref="newSessionAllDropdownRef"
                    class="new-session-dropdown"
                    placement="top-end"
                    @wa-select="handleNewSessionSelect"
                >
                    <wa-button
                        id="new-session-all-projects-button"
                        slot="trigger"
                        variant="brand"
                        appearance="accent"
                        size="small"
                    >
                        <wa-icon slot="end" name="chevron-up"></wa-icon>
                        <wa-icon name="plus"></wa-icon>
                        <span>New session</span>
                    </wa-button>
                    <wa-dropdown-item value="__new_project__">
                        <wa-icon slot="icon" name="plus"></wa-icon>
                        New project
                    </wa-dropdown-item>

                    <!-- Workspace projects first (when workspace active) -->
                    <template v-if="splitNamedProjects.prioritized.length || splitFlatTree.prioritized.length">
                        <wa-divider></wa-divider>
                        <wa-dropdown-item v-if="activeWsLabel" disabled class="section-header-item"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWsLabel }}</wa-dropdown-item>
                    </template>
                    <template v-for="p in splitNamedProjects.prioritized" :key="p.id">
                        <wa-dropdown-item :value="p.id" class="project-picker-row">
                            <ProjectBadge :project-id="p.id" />
                            <WorktreeButton v-if="p.git_root" slot="details" @create="openWorktreeDialog(p)" />
                        </wa-dropdown-item>
                        <WorktreePickerRows
                            :parent-id="p.id"
                            :worktrees="pickableWorktreesOf(p.id)"
                            :expanded="isNewSessionWorktreesExpanded(p.id)"
                            :base-depth="0"
                        />
                    </template>
                    <template v-for="item in splitFlatTree.prioritized" :key="'wsp-' + item.key">
                        <wa-dropdown-item
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-dropdown-item"
                        >
                            <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                {{ item.segment }}
                            </span>
                        </wa-dropdown-item>
                        <template v-else>
                            <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                                <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    <ProjectBadge :project-id="item.project.id" />
                                </span>
                                <WorktreeButton v-if="item.project.git_root" slot="details" @create="openWorktreeDialog(item.project)" />
                            </wa-dropdown-item>
                            <WorktreePickerRows
                                :parent-id="item.project.id"
                                :worktrees="pickableWorktreesOf(item.project.id)"
                                :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                                :base-depth="item.depth"
                            />
                        </template>
                    </template>

                    <!-- Other projects -->
                    <template v-if="activeWsLabel && (splitNamedProjects.others.length || splitFlatTree.others.length)">
                        <wa-divider></wa-divider>
                        <wa-dropdown-item disabled class="section-header-item">Other projects</wa-dropdown-item>
                    </template>
                    <wa-divider v-else-if="splitNamedProjects.others.length"></wa-divider>
                    <template v-for="p in splitNamedProjects.others" :key="p.id">
                        <wa-dropdown-item :value="p.id" class="project-picker-row">
                            <ProjectBadge :project-id="p.id" />
                            <WorktreeButton v-if="p.git_root" slot="details" @create="openWorktreeDialog(p)" />
                        </wa-dropdown-item>
                        <WorktreePickerRows
                            :parent-id="p.id"
                            :worktrees="pickableWorktreesOf(p.id)"
                            :expanded="isNewSessionWorktreesExpanded(p.id)"
                            :base-depth="0"
                        />
                    </template>

                    <!-- Other unnamed projects (flattened tree) -->
                    <wa-divider v-if="splitFlatTree.others.length"></wa-divider>
                    <template v-for="item in splitFlatTree.others" :key="item.key">
                        <wa-dropdown-item
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-dropdown-item"
                        >
                            <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                {{ item.segment }}
                            </span>
                        </wa-dropdown-item>
                        <template v-else>
                            <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                                <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    <ProjectBadge :project-id="item.project.id" />
                                </span>
                                <WorktreeButton v-if="item.project.git_root" slot="details" @create="openWorktreeDialog(item.project)" />
                            </wa-dropdown-item>
                            <WorktreePickerRows
                                :parent-id="item.project.id"
                                :worktrees="pickableWorktreesOf(item.project.id)"
                                :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                                :base-depth="item.depth"
                            />
                        </template>
                    </template>
                </wa-dropdown>
                <AppTooltip v-if="isAllProjectsMode" for="new-session-all-projects-button">Create a new session</AppTooltip>
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-footer">
                <div v-if="quotaHasUsage && quotaComputed" ref="usageBlockRef" class="sidebar-footer-usage">
                    <wa-icon
                        v-if="currentUsageProviderIcon"
                        id="usage-provider-icon"
                        class="usage-provider-icon"
                        :class="{ 'usage-provider-icon-clickable': hasMultipleUsageProviders }"
                        family="brands"
                        :name="currentUsageProviderIcon"
                        @click="hasMultipleUsageProviders && cycleUsageProvider()"
                    ></wa-icon>
                    <AppTooltip v-if="currentUsageProviderIcon" for="usage-provider-icon" hoist force>
                        <div class="usage-provider-tooltip">
                            <span class="usage-provider-tooltip-name">{{ currentUsageProviderLabel }} usage</span>
                            <span v-if="hasMultipleUsageProviders" class="usage-provider-tooltip-hint">Click to switch to the next provider</span>
                        </div>
                    </AppTooltip>
                    <div id="quota-five-hour" class="usage-quota" v-if="quotaFiveHour">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="Math.min(quotaFiveHour.utilization ?? 0, 100)"
                            :style="{ '--indicator-color': quotaFiveHourRingColor }"
                        ><span class="wa-font-weight-bold">{{ Math.round(quotaFiveHour.utilization ?? 0) }}%</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">5h</span>
                            <wa-relative-time v-if="quotaFiveHour.resetsAt" class="usage-quota-reset" :date.prop="resetsAtToDate(quotaFiveHour.resetsAt)" format="narrow" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaFiveHour" for="quota-five-hour" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-tooltip-title">{{ currentUsageProviderLabel }} usage — 5h</div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Usage</span><span>{{ (quotaFiveHour.utilization ?? 0).toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-note" v-if="!quotaFiveHour.resetsAt"><wa-icon name="info-circle"></wa-icon> Period not started yet</div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.timePct != null"><span class="quota-tooltip-label">Time elapsed</span><span>{{ quotaFiveHour.timePct.toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.burnRate != null"><span class="quota-tooltip-label">Burn rate</span><span>{{ (quotaFiveHour.burnRate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="quotaFiveHourCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaFiveHourCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.recentLong.rate != null && !quotaFiveHour.recentLong.isFallback"><span class="quota-tooltip-label" style="margin-left: .5rem;"> - last {{ formatRecentDelta(quotaFiveHour.recentLong.deltaMs, false) }}</span><span>{{ (quotaFiveHour.recentLong.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.recentShort.rate != null && !quotaFiveHour.recentShort.isFallback && formatRecentDelta(quotaFiveHour.recentShort.deltaMs, false) !== formatRecentDelta(quotaFiveHour.recentLong.deltaMs, false)"><span class="quota-tooltip-label" style="margin-left: .5rem;"> - last {{ formatRecentDelta(quotaFiveHour.recentShort.deltaMs, false) }}</span><span>{{ (quotaFiveHour.recentShort.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.resetsAt"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(quotaFiveHour.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaFiveHourCost && quotaFiveHourCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaFiveHourCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 5h</span><CostDisplay :cost="quotaFiveHourCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaFiveHourCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 5h estimate</div>
                            </template>
                            <div class="quota-tooltip-buttons wa-light">
                                <wa-button v-if="usageExternalLink" size="small" variant="brand" :appearance="quotaButtonAppearance" :href="usageExternalLink.url" target="_blank" rel="noopener"><wa-icon slot="start" name="up-right-from-square"></wa-icon>{{ usageExternalLink.label }}</wa-button>
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" @click="openUsageGraph('five-hour')"><wa-icon slot="start" name="chart-line"></wa-icon>View graph</wa-button>
                            </div>
                        </div>
                    </AppTooltip>
                    <div id="quota-seven-day" class="usage-quota" v-if="quotaSevenDay">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="Math.min(quotaSevenDay.utilization ?? 0, 100)"
                            :style="{ '--indicator-color': quotaSevenDayRingColor }"
                        ><span class="wa-font-weight-bold">{{ Math.round(quotaSevenDay.utilization ?? 0) }}%</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">7d</span>
                            <wa-relative-time v-if="quotaSevenDay.resetsAt" class="usage-quota-reset" :date.prop="resetsAtToDate(quotaSevenDay.resetsAt)" format="narrow" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaSevenDay" for="quota-seven-day" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-tooltip-title">{{ currentUsageProviderLabel }} usage — 7d</div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Usage</span><span>{{ (quotaSevenDay.utilization ?? 0).toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-note" v-if="!quotaSevenDay.resetsAt"><wa-icon name="info-circle"></wa-icon> Period not started yet</div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.timePct != null"><span class="quota-tooltip-label">Time elapsed</span><span>{{ quotaSevenDay.timePct.toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.burnRate != null"><span class="quota-tooltip-label">Burn rate</span><span>{{ (quotaSevenDay.burnRate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="quotaSevenDayCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaSevenDayCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.recentLong.rate != null && !quotaSevenDay.recentLong.isFallback"><span class="quota-tooltip-label" style="margin-left: .5rem;"> - last {{ formatRecentDelta(quotaSevenDay.recentLong.deltaMs, true) }}</span><span>{{ (quotaSevenDay.recentLong.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.recentShort.rate != null && !quotaSevenDay.recentShort.isFallback && formatRecentDelta(quotaSevenDay.recentShort.deltaMs, true) !== formatRecentDelta(quotaSevenDay.recentLong.deltaMs, true)"><span class="quota-tooltip-label" style="margin-left: .5rem;"> - last {{ formatRecentDelta(quotaSevenDay.recentShort.deltaMs, true) }}</span><span>{{ (quotaSevenDay.recentShort.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.resetsAt"><span class="quota-tooltip-label">Reset</span><span :title="formatResetTimePrecise(quotaSevenDay.resetsAt)">{{ formatResetTime(quotaSevenDay.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaSevenDayCost && quotaSevenDayCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaSevenDayCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 7d</span><CostDisplay :cost="quotaSevenDayCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaSevenDayCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 7d estimate</div>
                            </template>
                            <div class="quota-tooltip-buttons wa-light">
                                <wa-button v-if="usageExternalLink" size="small" variant="brand" :appearance="quotaButtonAppearance" :href="usageExternalLink.url" target="_blank" rel="noopener"><wa-icon slot="start" name="up-right-from-square"></wa-icon>{{ usageExternalLink.label }}</wa-button>
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" @click="openUsageGraph('seven-day')"><wa-icon slot="start" name="chart-line"></wa-icon>View graph</wa-button>
                            </div>
                        </div>
                    </AppTooltip>
                    <div id="quota-extra-usage" class="usage-quota" v-if="quotaExtraUsage">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="quotaExtraUsageRingValue"
                            :style="{ '--indicator-color': quotaExtraUsageRingColor }"
                        ><span class="wa-font-weight-bold">{{ quotaExtraUsageRingLabel }}</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">Extra usage</span>
                            <wa-relative-time v-if="quotaExtraUsage.utilization != null" class="usage-quota-reset" :date.prop="extraUsageResetDate()" format="narrow" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaExtraUsage" for="quota-extra-usage" hoist force>
                        <div class="quota-tooltip">
                            <template v-if="quotaExtraUsage.utilization != null">
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Used</span><span>{{ quotaExtraUsage.usedCredits ?? 0 }} credits</span></div>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Monthly limit</span><span>{{ quotaExtraUsage.monthlyLimit ?? '?' }} credits</span></div>
                            </template>
                            <template v-else-if="quotaExtraUsage.remainingCredits != null">
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Remaining</span><span>{{ Math.round(quotaExtraUsage.remainingCredits) }} credits</span></div>
                            </template>
                            <div v-if="quotaExtraUsage.utilization != null" class="quota-tooltip-row"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(extraUsageResetDate()) }}</span></div>
                            <div class="quota-tooltip-buttons wa-light">
                               <wa-button v-if="usageExternalLink" size="small" variant="brand" :appearance="quotaButtonAppearance" :href="usageExternalLink.url" target="_blank" rel="noopener" class="quota-stale-button"><wa-icon slot="start" name="up-right-from-square"></wa-icon>{{ usageExternalLink.label }}</wa-button>
                            </div>
                        </div>
                    </AppTooltip>
                    <wa-icon v-if="quotaIsStale" id="quota-stale-warning" name="triangle-exclamation" class="quota-stale-icon"></wa-icon>
                    <AppTooltip v-if="quotaIsStale" for="quota-stale-warning" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-stale-header"><wa-icon name="triangle-exclamation" class="quota-stale-header-icon"></wa-icon><span>Data may be outdated</span></div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Last update</span><span>{{ quotaLastUpdateFormatted }}</span></div>
                            <div class="quota-tooltip-buttons wa-light">
                                <wa-button v-if="canRefreshUsage" size="small" variant="brand" :appearance="quotaButtonAppearance" :loading="usageRefreshing" :disabled="usageRefreshing" class="quota-stale-button" @click="refreshUsage()"><wa-icon slot="start" name="arrow-rotate-right"></wa-icon>Refresh now</wa-button>
                                <wa-button v-if="usageExternalLink" size="small" variant="brand" :appearance="quotaButtonAppearance" :href="usageExternalLink.url" target="_blank" rel="noopener" class="quota-stale-button"><wa-icon slot="start" name="up-right-from-square"></wa-icon>{{ usageExternalLink.label }}</wa-button>
                            </div>
                        </div>
                    </AppTooltip>
                </div>

                <template v-for="(entry, index) in unauthenticatedProviders" :key="entry.provider">
                    <wa-divider v-if="index === 0 && quotaHasUsage && quotaComputed"></wa-divider>
                    <wa-divider v-else-if="index > 0"></wa-divider>
                    <div class="sidebar-footer-provider-auth">
                        <wa-callout variant="warning" size="small">
                            <div class="sidebar-footer-provider-auth-row">
                                <span class="sidebar-footer-provider-auth-text">{{ entry.label }} CLI not authenticated. Run <code class="copyable" title="Click to copy" @click="copyLoginCommand(entry.loginCommand)">{{ entry.loginCommand }}</code>.</span>
                                <div class="sidebar-footer-provider-auth-buttons">
                                    <wa-button
                                        v-if="entry.canDisable"
                                        size="small"
                                        variant="danger"
                                        appearance="outlined"
                                        @click="disableProvider(entry.provider)"
                                    >
                                        <wa-icon name="power-off"></wa-icon>Disable provider
                                    </wa-button>
                                    <wa-button
                                        size="small"
                                        variant="warning"
                                        appearance="outlined"
                                        @click="launchProviderAuthInTerminal(entry.loginCommand)"
                                    >
                                        <wa-icon name="terminal"></wa-icon>Launch in terminal
                                    </wa-button>
                                    <wa-button
                                        size="small"
                                        variant="warning"
                                        appearance="outlined"
                                        :disabled="authChecking.has(entry.provider)"
                                        @click="recheckProviderAuth(entry.provider)"
                                    >Check again</wa-button>
                                </div>
                            </div>
                        </wa-callout>
                    </div>
                </template>

                <wa-divider></wa-divider>

                <div class="sidebar-footer-buttons">
                    <!-- Sidebar Toggle button (label for hidden checkbox, wa-button inside for styling) -->
                    <label for="sidebar-toggle-state" class="sidebar-toggle" id="sidebar-toggle-label">
                        <span class="sidebar-backdrop"></span>
                        <wa-button id="sidebar-toggle-button" variant="neutral" appearance="filled-outlined" size="small">
                            <wa-icon class="icon-collapse" name="angles-left"></wa-icon>
                            <wa-icon class="icon-expand" name="angles-right"></wa-icon>
                        </wa-button>
                    </label>
                    <AppTooltip for="sidebar-toggle-label">Toggle sidebar</AppTooltip>

                    <!-- Placeholder to occupy the same space a the sidebar toggle button that is absolute for goot reasons -->
                    <wa-button variant="neutral" appearance="filled-outlined" size="small" style="visibility: hidden; pointer-events: none"><wa-icon name="angles-left"></wa-icon></wa-button>

                    <!-- Opens the command palette (mouse/touch access to the Cmd/Ctrl+K
                         shortcut); folds away on its own when the sidebar is too narrow. -->
                    <CommandPaletteButton />

                    <SettingsPopover />
                </div>
            </div>

        </aside>

        <!-- Main content area -->
        <main slot="end" class="main-content">
            <div v-show="sessionId" class="session-content">
                <router-view v-slot="{ Component }">
                    <KeepAlive :max="settingsStore.getMaxCachedSessions">
                        <component :is="Component" :key="route.params.sessionId" />
                    </KeepAlive>
                </router-view>
            </div>
            <div v-show="!sessionId" class="project-detail-content">
                <KeepAlive>
                    <ProjectDetailPanel :project-id="effectiveProjectId" :active="!sessionId" :key="effectiveProjectId" />
                </KeepAlive>
            </div>
        </main>
    </wa-split-panel>

    <!-- Shared rename dialog (single instance for sidebar + session header) -->
    <SessionRenameDialog
        ref="renameDialogRef"
        :session="sessionToRename"
    />

    <!-- Create project dialog (opened from "New session" dropdown) -->
    <ProjectEditDialog ref="createProjectDialogRef" @saved="handleProjectCreated" />
    <WorktreeDialog ref="worktreeDialogRef" @resolved="handleWorktreeResolved" />

    <!-- Edit project dialog (opened from a selector row's "…" menu) -->
    <ProjectEditDialog ref="sidebarProjectEditRef" :project="sidebarEditingProject" />

    <!-- Usage graph dialog -->
    <UsageGraphDialog ref="usageGraphDialogRef" :provider="currentUsageProvider" />

    <!-- Workspace management dialog -->
    <WorkspaceManageDialog ref="manageWorkspacesDialogRef" />

    <!-- Bulk archive confirmation dialog -->
    <BulkArchiveConfirmDialog
        v-model:open="bulkArchiveDialog.open"
        :preset="bulkArchiveDialog.preset"
        :scope="currentBulkArchiveScope"
        :title-query="trimmedSearchQuery"
        :include-archived-projects="showArchivedProjects"
        @archived="handleBulkArchived"
    />
    </div>
</template>

<style scoped>
.project-view-wrapper {
    height: 100dvh;
}

/* Hidden checkbox for CSS-only toggle */
.sidebar-toggle-checkbox {
    position: absolute;
    opacity: 0;
    pointer-events: none;
}

.project-view {
    height: 100%;
    --min: 20px;
    --max: 500px;
    transition: grid-template-columns var(--transition-duration) ease;
    &::part(divider) {
        z-index: 2;
        transition: opacity var(--transition-duration) ease;
    }
}

wa-split-panel::part(divider) {
    /* same color/width as normal dividers */
    background-color: var(--wa-color-surface-border);
    width: var(--divider-size);
}
/* Divider handle: hidden by default, shown only on touch devices */
.divider-handle {
    color: var(--wa-color-surface-border);
    display: none;
    scale: 3;
}

@media (pointer: coarse) {
    .divider-handle {
        display: inline;
    }
}

.sidebar {
    --transition-duration: .3s;
    height: 100dvh;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
    position: relative;
    /* Enable container queries on the sidebar */
    container-type: inline-size;
    container-name: sidebar;
}

.sidebar-header {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    justify-content: stretch;
    padding: var(--wa-space-s);
    gap: var(--wa-space-s);
    background: var(--main-header-footer-bg-color);
}

.sidebar-header-row {
    width: 100%;
    display: flex;
    justify-content: stretch;
    align-items: center;
    gap: var(--wa-space-s);
}

.session-search {
    flex: 1;
    min-width: 3.5rem;
    max-width: 100%;
    &:hover {
        z-index: 10;
        min-width: min(10rem, calc(100vw - 100px));
    }
}

.search-advanced-button {
    flex-shrink: 0;
}

.session-options-dropdown {
    flex-shrink: 0;
    &::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}

/* Secondary hint line under a dropdown-item label (same style as the
   "scroll to selected file" subtitle in the files tab root selector). */
.session-option-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.project-selector {
    /* Width reserved for each row's "…" actions overlay button. Inherited by the
       row components (ProjectSelectorRow, WorktreeSelectorRows) so the menu and
       the worktree-header spacer all reserve the exact same width and keep every
       row's indicators aligned. */
    --selector-row-action-size: 1.75rem;
    /* Right padding applied to every list row (overrides the wa-dropdown-item
       default of 1em). The "…" overlay is pinned at this same distance so it
       stays flush with each row's content edge. */
    --selector-row-padding-inline-end: 0.5em;
    flex: 1;
    overflow: hidden;
    display: inline-flex;
    max-width: min(50rem, calc(100vw - 100px));
    &::part(menu) {
       max-width: min(50rem, calc(100vw - 2rem)) !important
    }
}
@media (width < 400px) {
    .project-selector {
        &::part(menu) {
            width: min(50rem, calc(100vw - 2rem)) !important
        }
    }
}


.project-selector-trigger {
    width: 100%;
    background: var(--wa-color-surface-default);
    &::part(base) {
        justify-content: space-between;
    }
    &::part(start) {
        display: none;
    }
    &::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
        overflow: hidden;
    }
}
.project-selector {
    &:hover, &[open] {
        overflow: visible;
        .project-selector-trigger {
            z-index: 11;
        }
    }
}

@container sidebar (width <= 13rem) {
    .project-selector-trigger {
        &::part(base) {
            padding-inline: var(--wa-space-xs);
        }
        .project-selector-caret {
            margin-inline-start: var(--wa-space-3xs);
        }
    }
}

.project-selector-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    & > wa-icon {
        margin-right: var(--wa-space-2xs);
    }
}

.selected-project-dot {
    width: 0.75em;
    height: 0.75em;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

/* Tighter right padding for every list row (projects, workspaces, worktree
   headers, section headers, …) than the wa-dropdown-item default of 1em, so the
   content and the "…" overlay sit closer to the right edge. Scoped to direct
   children only — the nested "…" submenu items keep their own padding. */
.project-selector > :deep(wa-dropdown-item) {
    padding-inline-end: var(--selector-row-padding-inline-end);
}

/* Archived workspace rows: dimmed so they read as de-emphasized vs active ones
   when "show archived workspaces" surfaces them (matches ProjectSelectorRow's
   archived project rows). */
.selector-row-archived {
    opacity: 0.55;
}

.selector-item-content {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    gap: var(--wa-space-xs);
    /* Reserve room for the absolutely-positioned "…" overlay (button width +
       gap) so the indicators stay put and aligned with the worktree-header
       spacer. */
    padding-inline-end: calc(var(--selector-row-action-size, 1.5rem) + var(--wa-space-xs));
}

.selector-item-indicators {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    margin-left: auto;
    flex-shrink: 0;
}

/* Per-row "…" actions menu on workspace rows, rendered as a near-full-height
   overlay pinned to the right of the row. The wa-dropdown-item host is
   position:relative and adds vertical padding; without this overlay that padding
   band would be "dead" space where a click selects the workspace instead of
   opening the menu — making the button easy to miss. inset-block leaves a 1px gap
   top/bottom so the button stays visually separated from the row edges;
   inset-inline-end matches each row's right padding so the button stays flush
   with the content edge. */
.row-menu {
    position: absolute;
    inset-block: 1px;
    inset-inline-end: var(--selector-row-padding-inline-end, 0.5em);
    display: flex;
    align-items: stretch;
    justify-content: center;
    flex-shrink: 0;
    width: var(--selector-row-action-size, 1.5rem);
}

.row-menu-trigger {
    opacity: 0.55;
    font-size: var(--wa-font-size-s);
    transition: opacity 0.12s ease;
}

.row-menu-trigger::part(base) {
    padding: 0;
    min-height: 0;
    width: var(--selector-row-action-size, 1.5rem);
    /* Fill the full-height .row-menu overlay so the entire band opens the menu. */
    height: 100%;
}

wa-dropdown-item:hover .row-menu-trigger,
.row-menu:focus-within .row-menu-trigger {
    opacity: 1;
}

.tree-folder-dropdown-item {
    opacity: 1;
    cursor: default;
}

.section-header-item {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 1;
    cursor: default;
    color: var(--wa-color-text-quiet);
    wa-icon {
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-text-normal);
        margin-inline: 0.2em;
    }
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    /* Intermediate directory nodes are not projects — italicize them so they
       read as grouping folders, distinct from the unnamed-project leaves below. */
    font-style: italic;
    /* Folder rows live inside a disabled <wa-dropdown-item>, whose host sets
       `pointer-events: none` — which would suppress the native `title` tooltip
       showing the full path. Re-enable pointer events on the label itself so the
       tooltip works on hover (the row stays non-selectable: it carries no value). */
    pointer-events: auto;
}

.sidebar wa-divider {
    flex-shrink: 0;
    --width: var(--divider-size);
    --spacing: 0;
}

.sidebar-sessions {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 0;
    display: flex;
    flex-direction: column;
    position: relative;
}

.main-content {
    flex: 1;
    min-width: 0;
    height: 100%;
    overflow: hidden;
    background: var(--wa-color-surface-default);
    z-index: 1;
    container-type: inline-size;
    container-name: main-content;
}

.session-content,
.project-detail-content {
    height: 100%;
}

.sessions-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Project rows of the two "New session" dropdowns: reserve right padding for
   the absolutely-positioned "new worktree" overlay button (WorktreeButton:
   2.25rem wide, pinned 0.5em from the edge), so long project names never run
   under it. Applied to every project row (git or not) so all rows keep the
   same content width. */
.project-picker-row {
    padding-inline-end: calc(2.25rem + 0.5em + var(--wa-space-2xs));
}

/* Floating "New session" split button (single project mode) */
.new-session-split-button {
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;

    /* Style the main button label */
    & > wa-button::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

    /* Visual separator between main button and dropdown trigger */
    & wa-dropdown wa-button::part(base) {
        border-left: 1px solid rgba(255, 255, 255, 0.3);
    }

    /* Limit dropdown menu height: set the variable directly on #menu (via ::part)
       so it overrides the value inherited from wa-popup's inline style */
    & wa-dropdown::part(menu) {
        --auto-size-available-height: 50dvh;
    }
}

/* New session dropdown (for All Projects mode) - floating like the button */
.new-session-dropdown {
    /* Override display:contents to allow absolute positioning */
    display: block;
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;
    /* Only take the width needed by the trigger button */
    width: fit-content;

    /* Limit dropdown menu height: set the variable directly on #menu (via ::part)
       so it overrides the value inherited from wa-popup's inline style */
    &::part(menu) {
        --auto-size-available-height: 50dvh;
    }

    /* Style the trigger button label */
    & > wa-button::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}


@container sidebar (width <= 13rem) {
    /* Split button in narrow sidebar */
    .new-session-split-button {
        & > wa-button {
            &::part(base) {
                padding: var(--wa-space-s);
            }
            & > span {
                display: none;
            }
        }
    }
    /* Dropdown button in All Projects mode */
    .new-session-dropdown > wa-button {
        &::part(base) {
            padding: var(--wa-space-s);
        }
        &::part(caret) {
            display: none;
        }
        & > span {
            display: none;
        }
    }
}

.sidebar-footer {
    flex-shrink: 0;
}

.sidebar-footer-usage {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-evenly;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);
}

.sidebar-footer-provider-auth {
    padding: var(--wa-space-xs) var(--wa-space-s);
}

.sidebar-footer-provider-auth-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.sidebar-footer-provider-auth-text {
    flex: 1 1 auto;
    min-width: 0;
}

.sidebar-footer-provider-auth-text code {
    font-family: var(--wa-font-mono);
    font-size: 0.95em;
    padding: 0 var(--wa-space-3xs);
    background: var(--wa-color-warning-fill-normal);
    border-radius: var(--wa-border-radius-s);
}

.sidebar-footer-provider-auth-text code.copyable {
    cursor: pointer;
}

.sidebar-footer-provider-auth-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    max-width: 90%;
    wa-button {
        &::part(base) {
            height: auto;
            min-height: var(--wa-form-control-height);
        }
        &::part(label) {
            display: flex;
            gap: .5em;
            white-space: break-spaces;
            line-height: calc(var(--wa-form-control-height) - var(--border-width) * 2);
        }
    }
}

.usage-quota {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    max-width: 100%;
}

.usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

.usage-provider-icon {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-neutral-content);
}

.usage-provider-icon-clickable {
    cursor: pointer;
}

.usage-provider-tooltip {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-3xs);
}

.usage-provider-tooltip-name {
    font-weight: var(--wa-font-weight-bold);
}

.usage-provider-tooltip-hint {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-neutral-muted);
}

.usage-quota-info {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
}

/* Remove text in narrow sidebar */
@container sidebar (width <= 13rem) {
    .sidebar-footer-usage {
        column-gap: var(--wa-space-3xs);
    }
    .usage-quota-info {
        display: none;
    }
}

.usage-quota-label {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-neutral-content);
}

.usage-quota-reset {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-neutral-muted);
}

.quota-tooltip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.quota-tooltip-title {
    font-weight: var(--wa-font-weight-bold);
    text-align: center;
    padding-bottom: var(--wa-space-3xs);
    border-bottom: 1px solid var(--wa-color-neutral-border-quiet);
}

.quota-tooltip-row {
    display: flex;
    justify-content: space-between;
    gap: var(--wa-space-l);
    white-space: nowrap;
}

.quota-tooltip-label {
    font-weight: var(--wa-font-weight-bold);
}

.quota-tooltip-divider {
    --spacing: var(--wa-space-2xs);
}

.quota-tooltip-note {
    font-size: var(--wa-font-size-2xs);
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.quota-tooltip-row-danger {
    color: var(--wa-color-danger);
}

.quota-stale-icon {
    color: var(--wa-color-warning);
    font-size: var(--wa-font-size-l);
}

.quota-stale-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: var(--wa-font-weight-bold);
}

.quota-stale-header-icon {
    color: var(--wa-color-warning);
}

.quota-stale-button {
    align-self: stretch;
}

.quota-tooltip-buttons {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.quota-tooltip-buttons wa-button {
    width: 100%;
}

.sidebar-footer-buttons {
    flex-shrink: 0;
    display: flex;
    gap: var(--wa-space-s);
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-s);
    position: relative;
    background: var(--main-header-footer-bg-color);
}

/* Sidebar toggle label */
.sidebar-toggle {
    position: absolute;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
    z-index: 10;
    cursor: pointer;

    /* wa-button inside label: disable pointer events so clicks go to label */
    wa-button {
        pointer-events: none;
        wa-icon {
            position: relative;
            top: -1px;
        }
    }

    /* Default state: sidebar is expanded, show collapse icon */
    .icon-collapse {
        display: inline;
    }
    .icon-expand {
        display: none;
    }
}

/* Container query: when sidebar is collapsed (≤ 50px), show expand icon */
@container sidebar (width <= 50px) {
    .sidebar-toggle .icon-collapse {
        display: none;
    }

    .sidebar-toggle .icon-expand {
        display: inline;
    }
}

/* Desktop: checkbox checked = sidebar collapsed */
.project-view-wrapper:has(.sidebar-toggle-checkbox:checked) .project-view {
    grid-template-columns: 0 var(--divider-width) auto !important;
    &::part(divider) {
        opacity: 0;
        pointer-events: none;
    }
}

/* Media query: mobile behavior - sidebar as overlay */
@media (width < 640px) {
    /* Use dynamic viewport height on mobile to account for browser chrome */
    .project-view-wrapper {
        height: 100dvh;
    }

    /* Split panel always shows content at full width, so replace grid of project view by a block, sidebar will be an overlay */
    .project-view {
        display: block;
        &::part(divider) {
            display: none;
        }

    }

    /* Sidebar becomes a fixed drawer */
    .sidebar {
        --sidebar-width: min(300px, 80vw);
        position: absolute;
        left: 0;
        top: 0;
        width: var(--sidebar-width);
        height: 100dvh;
        z-index: 100;
        transform: translateX(-100%);
        transition: transform var(--transition-duration) ease;
        box-shadow: var(--wa-shadow-xl);
        border-right: solid var(--wa-color-neutral-border-normal) 0.25rem;
    }

    /* Toggle button sticks out from the sidebar when closed */
    .sidebar-toggle {
        /* Position at right edge of sidebar, offset to stick out */
        transform: translateX(var(--sidebar-width)) translateY(4px);
        transition: transform var(--transition-duration) ease;
        .icon-collapse {
            display: none;
        }
        .icon-expand {
            display: inline;
        }
    }

    /* Backdrop positioned sticky inside the label
       so clicking on it closes the sidebar */
    .sidebar-toggle {
        /* try to reproduce the same size as the button
          else the label expands because of the backdrop sticky positioned */
        --checkbox-label-size: calc(var(--wa-form-control-height) - var(--wa-shadow-offset-y-s) * 2 + 2px);
        height: var(--checkbox-label-size);
        width: var(--checkbox-label-size);
        wa-button {
            position: absolute;
            top: 0;
            left: 0;
        }
        .sidebar-backdrop {
            display: block;
            position: sticky;
            top: 0;
            left: 0;
            /* Its top starts from the label so we have to move it up and on the right of the sidebar */
            --translate-x: 0px;
            translate: calc(var(--translate-x) - var(--wa-space-s)) calc(-100dvh + var(--checkbox-label-size) + var(--wa-space-s));
            width: 100vw;
            height: 100dvh;
            pointer-events: none;
            transition: background var(--transition-duration) ease, translate var(--transition-duration) ease;
            background: transparent;
        }
    }

    /* When sidebar is open, button goes back inside */
    .project-view-wrapper:has(.sidebar-toggle-checkbox:checked)  {
        .sidebar {
            transform: translateX(0);
        }

         .sidebar-toggle {
            transform: translateX(0) translateY(4px);
            .icon-collapse {
                display: inline;
            }
            .icon-expand {
                display: none;
            }

            .sidebar-backdrop {
                pointer-events: all;
                background: rgba(0, 0, 0, 0.5);
                --translate-x: var(--sidebar-width);
            }
        }


    }
}
</style>
