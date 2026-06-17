<script setup>
import { computed, watch, ref, reactive, readonly, provide, inject, onActivated, onDeactivated, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { getProviderHelpers } from '../providers'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { requestTitleSuggestion, notifySessionViewed, forceNotifySessionViewed, markSessionReadState, cancelSessionViewedThrottle } from '../composables/useWebSocket'
import { stopSessionProcess } from '../composables/useStopSessionProcess'
import { useDragHover } from '../composables/useDragHover'
import { useSessionLayout } from '../composables/useSessionLayout'
import { PROCESS_STATE } from '../constants'
import SessionHeader from '../components/session/detail/SessionHeader.vue'
import SessionItemsList from '../components/session/detail/SessionItemsList.vue'
import SessionContent from '../components/session/detail/SessionContent.vue'
import FilesPanel from '../components/files/FilesPanel.vue'
import GitPanel from '../components/git/GitPanel.vue'
import TerminalPanel from '../components/terminal/TerminalPanel.vue'
import OrchestrationPanel from '../components/orchestration/OrchestrationPanel.vue'
import SessionLayout from '../components/session/layout/SessionLayout.vue'
import TabPlacementMenu from '../components/session/layout/TabPlacementMenu.vue'
import AppTooltip from '../components/ui/AppTooltip.vue'
import ProcessIndicator from '../components/ui/ProcessIndicator.vue'
import CodeCommentsIndicator from '../components/ui/CodeCommentsIndicator.vue'
import { useCodeCommentsStore } from '../stores/codeComments'
import {
    buildFilesRouteParams,
    buildGitRouteParams,
    clearTabRouteParams,
    buildSessionBaseRouteName,
    buildSubagentRouteName,
    buildTabRouteName,
    buildTerminalRouteParams,
    decodePath,
    parseRouteString,
    parseRouteTermIndex,
} from '../utils/granularRoutes'
import { getAgentDisplayLabel } from '../utils/agentLabel'
import { focusChatPrimary, gotoChatFooterPanel } from '../utils/focusChat'
import { fileRootsFromStore } from '../utils/projectRoots'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const { registerCommands, unregisterCommands } = useCommandRegistry()

// Reference to session header for opening rename dialog
const sessionHeaderRef = ref(null)

// Reference to session items list for scroll compensation
const sessionItemsListRef = ref(null)

// Reference to FilesPanel for cross-tab file reveal
const filesPanelRef = ref(null)

// Reference to the Artifacts tab's FilesPanel (fixed root = session artifacts dir)
const artifactsPanelRef = ref(null)

const gitPanelRef = ref(null)
const terminalPanelRef = ref(null)

// ═══════════════════════════════════════════════════════════════════════════
// KeepAlive lifecycle: active state, listener setup/teardown
// ═══════════════════════════════════════════════════════════════════════════

const isActive = ref(true)

onMounted(() => {
    // Mark session as viewed on first render
    notifySessionViewed(sessionId.value, 'mounted')
    // Listen for tab keyboard shortcuts (dispatched by App.vue)
    window.addEventListener('twicc:tab-shortcut', handleTabShortcut)
    // Listen for live artifact file changes (dispatched by useWebSocket)
    window.addEventListener('twicc:artifact-files-changed', handleArtifactFilesChanged)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:tab-shortcut', handleTabShortcut)
    window.removeEventListener('twicc:artifact-files-changed', handleArtifactFilesChanged)
})

/**
 * The ArtifactsWatcher relayed changed file(s) under some session. Forward them
 * to this session's Artifacts panel for live-refresh, but only when this is the
 * matching, currently-active session view (avoids churning cached background
 * instances). Paths are relative to the session artifacts dir.
 */
function handleArtifactFilesChanged(e) {
    if (e.detail?.sessionId !== sessionId.value || !isActive.value) return
    artifactsPanelRef.value?.onArtifactFilesChanged(e.detail.paths || [])
}

onActivated(() => {
    isActive.value = true

    // Start observing compact tab overflow
    startCompactTabsObserver()

    // Register contextual session commands in the command palette
    registerSessionCommands()

    // Mark session as viewed when re-activated (KeepAlive navigation back)
    notifySessionViewed(sessionId.value, 'activated')

    // Re-resolve if the session disappeared from the store while this cached
    // instance was inactive — typically a draft we created was rebound to a
    // canonical id and deleted while the user was on another session. Without
    // this re-check, the back navigation lands on the loading spinner forever
    // because the setup-time resolve only ran once with the draft still alive.
    if (!session.value) {
        ensureSessionResolved()
    }
})

onDeactivated(() => {
    isActive.value = false

    // Force-send session_viewed to ensure last_viewed_at is fresh before leaving.
    // Without this, the throttle can cause last_viewed_at to be stale (set at navigation time)
    // while last_new_content_at was updated during viewing — making the session appear unread.
    forceNotifySessionViewed(sessionId.value, 'deactivated')

    // Stop observing compact tab overflow
    stopCompactTabsObserver()

    // Unregister contextual session commands from the command palette
    unregisterCommands(SESSION_COMMAND_IDS)

    // Cancel any pending drag-hover timer
    chatTabDragHover.cancel()
})

provide('sessionActive', readonly(isActive))

// ─── Cross-tab file reveal (Git → Files / Artifacts) ─────────────────────────

/**
 * Reveal a file in the right tab. A path inside the session's artifacts dir
 * opens in the Artifacts tab; everything else opens in the Files tab on the
 * matching root. Provided to descendant components (file links in tool uses,
 * markdown, patch entries, the Git diff "view in files" button).
 *
 * @param {string} absolutePath — the absolute filesystem path to reveal
 */
async function viewFileInFilesTab(absolutePath, { lineNum = null } = {}) {
    // Artifacts live outside the project file roots, in their own tab.
    // artifactsDir is only set when the session has artifacts (so the tab
    // exists), which naturally gates this branch.
    if (artifactsDir.value && absolutePath.startsWith(artifactsDir.value + '/')) {
        const relativePath = absolutePath.slice(artifactsDir.value.length + 1)
        navigateInTab('artifacts', buildFilesRouteParams({ rootKey: 'artifacts', filePath: relativePath }))
        await nextTick()
        await artifactsPanelRef.value?.revealFile(absolutePath, { lineNum })
        return
    }

    const project = store.getProject(session.value?.project_id)
    const roots = fileRootsFromStore(project, session.value, store)
    const match = roots.find(r => absolutePath.startsWith(r.path + '/'))
    const rootKey = match?.key
    const relativePath = match ? absolutePath.slice(match.path.length + 1) : undefined

    navigateInTab('files', buildFilesRouteParams({ rootKey, filePath: relativePath }))
    await nextTick()
    await filesPanelRef.value?.revealFile(absolutePath, { lineNum })
}

provide('viewFileInFilesTab', viewFileInFilesTab)

function insertTextAtCursor(text) {
    sessionItemsListRef.value?.insertTextAtCursor(text)
}
provide('insertTextAtCursor', insertTextAtCursor)

// Current session from route params
// IMPORTANT: these refs are captured at creation time (not reactive computeds
// from route.params) because with KeepAlive, the route changes globally when
// switching sessions. If they were reactive, ALL cached SessionView instances
// would see the NEW session's params, breaking deactivation hooks and item lookups.
// The KeepAlive key (route.params.sessionId) ensures each instance gets the correct
// value at creation time and keeps it permanently.
//
// filterProjectId is the project the sidebar filter was on when this SessionView
// was created. It is used only by router.push calls that rebuild the current
// URL, so that switching tabs (main / subagent / files / git / terminal) never
// changes the sidebar filter — even when the session lives in a different
// project than the filter (cross-filter artifact bookmarks, future pin cross-filter).
//
// projectId (declared further down, after `session`) is the project the session
// belongs to, driven by `session.project_id`. It is used for API calls, code-
// comments lookups, and WS payloads.
const filterProjectId = ref(route.params.projectId)
const sessionId = ref(route.params.sessionId)
const subagentId = computed(() => route.params.subagentId)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
const filesRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const filesRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
// Artifacts tab reuses the files route shape (rootKey + filePath).
const artifactsRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const artifactsRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
const gitRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const gitRouteCommitRef = computed(() => parseRouteString(route.params.commitRef))
const gitRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
const terminalRouteTermIndex = computed(() => parseRouteTermIndex(route.params.termIndex))

// Session data
const session = computed(() => store.getSession(sessionId.value))

// ─── Artifacts tab ───────────────────────────────────────────────────────────
// has_artifacts is monotonic (flips false->true once the session's
// <data_dir>/artifacts/<id>/ dir is non-empty; never reset). The tab browses
// that fixed dir through the standalone file API (apiPrefix '/api') with a
// server-side root restriction, exactly like the Files tab otherwise.
const hasArtifacts = computed(() => !!session.value?.has_artifacts)
const artifactsDir = computed(() => session.value?.artifacts_dir || null)
const artifactsExternalRoots = computed(() =>
    artifactsDir.value ? [{ key: 'artifacts', label: 'Artifacts', path: artifactsDir.value }] : []
)

// `sessionLoadError` drives the "not found" / "error" fallback in the template:
// - `null`: still loading, loaded successfully, or redirecting via draft alias
// - `'not-found'`: backend returned 404 — the session ID does not exist
// - `'error'`: network or server error — the user can try again by reloading
const sessionLoadError = ref(null)

// Resolve the session when it is missing from the store. Two paths:
// - Draft rebound to a canonical id (Codex flow when the bind happened while
//   the user was on another session — ``bindDraftSession`` skipped its inline
//   ``router.replace`` because ``onDraft`` was false, but still populated
//   ``draftAliases`` and deleted the draft). Redirect transparently via
//   ``router.replace`` so the user lands on the real session instead of a
//   "not found" screen. Preserves the forward history (replaceState only
//   touches the current history entry).
// - Otherwise fetch by id. Covers cross-filter deep links (the URL's
//   projectId is the sidebar filter, not the session's real project) and
//   direct artifact bookmarks into a project whose sessions haven't been loaded yet.
//   ``loadSessionById`` is idempotent.
//
// Called from setup (initial render) and from ``onActivated`` (cached KeepAlive
// instance whose session disappeared while it was inactive).
async function ensureSessionResolved() {
    if (session.value) {
        sessionLoadError.value = null
        return
    }

    const canonicalId = store.localState.draftAliases[sessionId.value]
    if (canonicalId) {
        router.replace({
            name: route.name,
            params: { ...route.params, sessionId: canonicalId },
            query: route.query,
        })
        return
    }

    try {
        const result = await store.loadSessionById(sessionId.value)
        if (!result) sessionLoadError.value = 'not-found'
    } catch {
        sessionLoadError.value = 'error'
    }
}

ensureSessionResolved()

// Session's project (data-driven). Stable per KeepAlive instance because
// sessionId is frozen and session.project_id is immutable for a given session.
// Used for API calls, code-comments lookups, WS payloads, and template props
// that identify the session's project (not the sidebar filter).
const projectId = computed(() => session.value?.project_id)

// Whether the session is in a git repository:
// - session has resolved git info (git_directory + git_branch from tool_use), OR
// - the project itself is inside a git repo (git_root resolved from project directory)
const hasGitRepo = computed(() =>
    (!!session.value?.git_directory && !!session.value?.git_branch)
    || !!store.getProject(session.value?.project_id)?.git_root
)

// Whether the session belongs to a spawned-session orchestration tree.
// ``spawn_root`` is set as soon as a session spawns its first child (it points
// to itself) or is itself spawned by another session — i.e. exactly when there
// is a topology worth showing. Drives the Orchestration tab's visibility.
const hasSpawnRoot = computed(() => !!session.value?.spawn_root)

// Code comments counts per tab
const filesCommentsCount = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'files')
)
const gitCommentsCount = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'git')
)
const chatCommentsCount = computed(() =>
    codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .filter(c => c.source === 'tool' && !c.subagentSessionId).length
)
function agentCommentsCount(agentSessionId) {
    return codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .filter(c => c.subagentSessionId === agentSessionId).length
}

// Tabs state - computed from store (automatically updates when session changes)
// Format: [{ id: 'agent-xxx', agentId: 'xxx' }, ...]
const openSubagentTabs = computed(() => {
    const saved = store.getSessionOpenTabs(sessionId.value)
    if (!saved) return []

    return saved.tabs
        .filter(id => id !== 'main' && id.startsWith('agent-'))
        .map(id => ({
            id,
            agentId: id.replace('agent-', '')
        }))
})

// Active tab ID ('main' for session, 'agent-xxx' for subagents, 'files'/'git'/'terminal' for tool tabs)
// Computed from route
const activeTabId = computed(() => {
    if (subagentId.value) {
        return `agent-${subagentId.value}`
    }
    const name = route.name
    if (name === 'session-files' || name === 'projects-session-files') return 'files'
    if (name === 'session-artifacts' || name === 'projects-session-artifacts') return 'artifacts'
    if (name === 'session-git' || name === 'projects-session-git') return 'git'
    if (name === 'session-terminal' || name === 'projects-session-terminal') return 'terminal'
    if (name === 'session-orchestration' || name === 'projects-session-orchestration') return 'orchestration'
    return 'main'
})

// All tabs for the compact header dropdown (includes labels, process state, comment counts)
const compactTabs = computed(() => {
    const tabs = [
        { id: 'main', label: 'Chat', commentsCount: chatCommentsCount.value }
    ]
    for (const tab of openSubagentTabs.value) {
        tabs.push({
            id: tab.id,
            label: `Agent "${getAgentTabLabel(tab.agentId)}"`,
            processState: store.getProcessState(tab.agentId) || null,
            commentsCount: agentCommentsCount(tab.agentId)
        })
    }
    tabs.push({ id: 'files', label: 'Files', commentsCount: filesCommentsCount.value })
    if (hasGitRepo.value) {
        tabs.push({ id: 'git', label: 'Git', commentsCount: gitCommentsCount.value })
    }
    tabs.push({ id: 'terminal', label: 'Terminal' })
    if (hasArtifacts.value) {
        tabs.push({ id: 'artifacts', label: 'Artifacts' })
    }
    if (hasSpawnRoot.value) {
        tabs.push({ id: 'orchestration', label: 'Orchestration' })
    }
    return tabs
})

// Redirect away from git tab if the session has no git repo
// (handles direct URL navigation and dynamic changes)
// Guards:
// - skip when deactivated (KeepAlive)
// - skip when route belongs to another session
// - skip when project data hasn't loaded yet (avoid premature redirect on
//   direct URL navigation — hasGitRepo depends on project.git_root which is
//   only available after loadProjects() completes)
watch([activeTabId, hasGitRepo], ([tabId, hasGit]) => {
    if (tabId === 'git' && !hasGit) {
        if (!isActive.value) return
        if (route.params.sessionId !== sessionId.value) return
        if (!store.getProject(session.value?.project_id)) return
        router.replace({
            name: buildSessionBaseRouteName(isAllProjectsMode.value),
            params: { projectId: filterProjectId.value, sessionId: sessionId.value },
            query: route.query,
        })
    }
}, { immediate: true })

// Redirect away from the artifacts tab if the session has no artifacts
// (handles direct URL navigation to /artifacts when none exist yet). Mirrors
// the git-tab guard; the ``session.value`` check avoids a premature redirect
// before the session row is loaded.
watch([activeTabId, hasArtifacts], ([tabId, hasArt]) => {
    if (tabId === 'artifacts' && !hasArt) {
        if (!isActive.value) return
        if (route.params.sessionId !== sessionId.value) return
        if (!session.value) return
        router.replace({
            name: buildSessionBaseRouteName(isAllProjectsMode.value),
            params: { projectId: filterProjectId.value, sessionId: sessionId.value },
            query: route.query,
        })
    }
}, { immediate: true })

// Redirect away from the orchestration tab if the session is not part of a
// spawned-session tree (handles direct URL navigation and a session that
// loses/never had a spawn_root). Mirrors the git-tab guard above; the
// ``session.value`` check avoids a premature redirect before the session row
// is loaded (when hasSpawnRoot is transiently false).
watch([activeTabId, hasSpawnRoot], ([tabId, hasRoot]) => {
    if (tabId === 'orchestration' && !hasRoot) {
        if (!isActive.value) return
        if (route.params.sessionId !== sessionId.value) return
        if (!session.value) return
        router.replace({
            name: buildSessionBaseRouteName(isAllProjectsMode.value),
            params: { projectId: filterProjectId.value, sessionId: sessionId.value },
            query: route.query,
        })
    }
}, { immediate: true })

function navigateInTab(tab, params = {}, method = 'push') {
    router[method]({
        name: buildTabRouteName({
            isAllProjectsMode: isAllProjectsMode.value,
            isSessionRoute: true,
            tab,
        }),
        params: clearTabRouteParams(tab, {
            projectId: filterProjectId.value,
            sessionId: sessionId.value,
            ...params,
        }),
        query: route.query,
    })
}

// While docking is active several tool panels are visible at once, but only the focused tab
// (activeTabId) owns the URL. User-initiated navigation (clicking a file / commit / root) is
// allowed through: combined with click-to-focus on a pane it focuses that tab and drives the
// URL. The Terminal panel is special — it *reactively* re-grabs the route (replaceToTerm)
// whenever it is visible but not the route owner, which would fight the focused tab in an
// infinite URL loop — so only its navigate is gated to the focused tab.
function ownsRoute(tabId) {
    return !layout.dockingRendered.value || activeTabId.value === tabId
}

function onFilesNavigate({ rootKey, filePath, replace }) {
    const params = buildFilesRouteParams({ rootKey, filePath })
    rememberToolTabRoute('files', params)
    navigateInTab('files', params, replace ? 'replace' : 'push')
}

function onArtifactsNavigate({ rootKey, filePath, replace }) {
    const params = buildFilesRouteParams({ rootKey, filePath })
    rememberToolTabRoute('artifacts', params)
    navigateInTab('artifacts', params, replace ? 'replace' : 'push')
}

function onGitNavigate({ rootKey, commitRef, filePath, replace }) {
    const params = buildGitRouteParams({ rootKey, commitRef, filePath })
    rememberToolTabRoute('git', params)
    navigateInTab('git', params, replace ? 'replace' : 'push')
}

function onTerminalNavigate({ termIndex, replace }) {
    if (!ownsRoute('terminal')) return
    const params = buildTerminalRouteParams({ termIndex })
    rememberToolTabRoute('terminal', params)
    navigateInTab('terminal', params, replace ? 'replace' : 'push')
}

const TOOL_TAB_IDS = ['files', 'artifacts', 'git', 'terminal', 'orchestration']

// Keep the last granular URL visited for each tool tab so switching away and back
// restores the previous state instead of resetting the panel to its base route.
const rememberedToolTabRoutes = {
    files: null,
    artifacts: null,
    git: null,
    terminal: null,
    // Orchestration has no granular sub-route; kept here so the generic
    // tool-tab navigation in switchToTab treats it uniformly.
    orchestration: null,
}

function getCurrentToolTabRouteParams(tabId) {
    if (tabId === 'files') {
        return buildFilesRouteParams({
            rootKey: filesRouteRootKey.value,
            filePath: filesRouteFilePath.value,
        })
    }

    if (tabId === 'artifacts') {
        return buildFilesRouteParams({
            rootKey: artifactsRouteRootKey.value,
            filePath: artifactsRouteFilePath.value,
        })
    }

    if (tabId === 'git') {
        return buildGitRouteParams({
            rootKey: gitRouteRootKey.value,
            commitRef: gitRouteCommitRef.value,
            filePath: gitRouteFilePath.value,
        })
    }

    if (tabId === 'terminal') {
        return buildTerminalRouteParams({
            termIndex: terminalRouteTermIndex.value,
        })
    }

    return null
}

function rememberToolTabRoute(tabId, params = getCurrentToolTabRouteParams(tabId)) {
    if (!TOOL_TAB_IDS.includes(tabId)) return
    rememberedToolTabRoutes[tabId] = params ?? {}
}

watch(
    [
        isActive,
        activeTabId,
        filesRouteRootKey,
        filesRouteFilePath,
        artifactsRouteRootKey,
        artifactsRouteFilePath,
        gitRouteRootKey,
        gitRouteCommitRef,
        gitRouteFilePath,
        terminalRouteTermIndex,
    ],
    ([active, tabId]) => {
        if (!active) return
        if (route.params.sessionId !== sessionId.value) return
        if (!TOOL_TAB_IDS.includes(tabId)) return
        rememberToolTabRoute(tabId)
    },
    { immediate: true }
)

/**
 * Navigate to a specific tab by panel name.
 * Used both by the wa-tab-group event handler and compact-mode tab buttons.
 * @param {string} panel - The panel name (e.g., 'main', 'agent-xxx', 'files', 'git', 'terminal')
 */
function switchToTab(panel) {
    // Ignore if already on this tab (avoid infinite loop)
    if (panel === activeTabId.value) return

    if (panel === 'main') {
        // Navigate to session without subagent
        router.push({
            name: buildSessionBaseRouteName(isAllProjectsMode.value),
            params: {
                projectId: filterProjectId.value,
                sessionId: sessionId.value
            },
            query: route.query,
        })
    } else if (panel.startsWith('agent-')) {
        // Navigate to subagent
        const agentId = panel.replace('agent-', '')
        router.push({
            name: buildSubagentRouteName(isAllProjectsMode.value),
            params: {
                projectId: filterProjectId.value,
                sessionId: sessionId.value,
                subagentId: agentId
            },
            query: route.query,
        })
    } else if (TOOL_TAB_IDS.includes(panel)) {
        navigateInTab(panel, rememberedToolTabRoutes[panel] ?? {})
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Dockable layout (opt-in). Until a tool tab is docked, the plain tab group below
// behaves exactly as before; once docked, the resolver-driven SessionLayout kicks in.
// ═══════════════════════════════════════════════════════════════════════════

// Root element of the docking area, measured by the composable.
const sessionLayoutRef = ref(null)

// Resolver input: the dockable tool tabs (chat is the fixed center anchor; subagents are
// center-only and not dockable, so they're excluded here).
const layoutTabs = computed(() => {
    const tabs = [{ id: 'main', label: 'Chat', icon: 'comments', fixedCenter: true }]
    tabs.push({ id: 'files', label: 'Files', icon: 'folder' })
    if (hasGitRepo.value) tabs.push({ id: 'git', label: 'Git', icon: 'code-branch' })
    tabs.push({ id: 'terminal', label: 'Terminal', icon: 'terminal' })
    if (hasArtifacts.value) tabs.push({ id: 'artifacts', label: 'Artifacts', icon: 'image' })
    if (hasSpawnRoot.value) tabs.push({ id: 'orchestration', label: 'Orchestration', icon: 'diagram-project' })
    return tabs
})

const layout = useSessionLayout({
    sessionId,
    containerRef: () => sessionLayoutRef.value?.$el,
    tabs: layoutTabs,
    routeActiveTabId: activeTabId,
})

const LAYOUT_TOOL_IDS = ['files', 'git', 'terminal', 'artifacts', 'orchestration']

// A tool tab is shown in the center strip unless it's currently docked.
function showInCenter(tabId) {
    return !layout.dockingRendered.value || layout.dockOf(tabId) === 'center'
}
function isCenterTab(tabId) {
    if (tabId === 'main' || tabId.startsWith('agent-')) return true
    return showInCenter(tabId)
}
// The center strip's active tab: the routed tab when it lives in the center, otherwise the
// last center tab (so focusing a docked tab doesn't blank the center).
const lastCenterTab = ref('main')
watch(activeTabId, (id) => { if (id && isCenterTab(id)) lastCenterTab.value = id }, { immediate: true })
const centerActiveTab = computed(() => isCenterTab(activeTabId.value) ? activeTabId.value : lastCenterTab.value)

// Whether a tool panel is the visible one at its destination (drives its :active prop).
function isToolTabShown(tabId) {
    if (showInCenter(tabId)) return centerActiveTab.value === tabId
    return layout.isToolPanelVisible(tabId)
}

// Click-to-focus for the center zone (mirror of DockRegion's): clicking the center content
// while a dock owns the URL restores the URL to the center's active tab. Tab clicks navigate
// on their own, so skip pointerdowns that land on the nav.
function onCenterPointerDown(event) {
    if (!layout.dockingRendered.value) return
    if (event.target?.closest?.('[slot="nav"]')) return
    switchToTab(centerActiveTab.value)
}

// Minimizing the dock that holds the focused tab would leave the URL on a now-hidden panel —
// hand focus back to the center's active tab.
function onLayoutMinimize(dockIds) {
    const focusedLeaving = dockIds.includes(layout.dockOf(activeTabId.value))
    layout.minimize(dockIds)
    if (focusedLeaving) switchToTab(centerActiveTab.value)
}

// Teleport target registry: logical key -> element. The center slot registers its tab-panel
// targets; dock regions / the overlay register theirs. Tool panels teleport to targetKeyForTab().
const layoutTargets = reactive({})
function registerLayoutTarget(key, el) { layoutTargets[key] = el }
function unregisterLayoutTarget(key) { delete layoutTargets[key] }
function toolTarget(tabId) { return layoutTargets[layout.targetKeyForTab(tabId)] || null }

// Stable ref callbacks for the center tab-panel targets (avoid re-running on every render).
const centerTargetSetters = Object.fromEntries(
    LAYOUT_TOOL_IDS.map((id) => [id, (el) => (el
        ? registerLayoutTarget(`center:${id}`, el)
        : unregisterLayoutTarget(`center:${id}`))])
)

/**
 * Navigate to a tab and collapse the compact header overlay.
 * Used by the compact-mode tab buttons inside the header slot.
 * @param {string} panel
 */
function switchToTabAndCollapse(panel) {
    switchToTab(panel)
    if (sessionHeaderRef.value?.isCompactExpanded) {
        sessionHeaderRef.value.isCompactExpanded = false
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: tab navigation (Alt+Shift+1-4, ←/→, ↑)
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Ordered list of all visible tabs (for sequential ←/→ navigation).
// Matches the visual order in the wa-tab-group: main, subagents, files, [git], terminal.
const orderedTabs = computed(() => {
    const tabs = ['main']
    for (const tab of openSubagentTabs.value) {
        tabs.push(tab.id)
    }
    tabs.push('files')
    if (hasGitRepo.value) tabs.push('git')
    tabs.push('terminal')
    if (hasArtifacts.value) tabs.push('artifacts')
    if (hasSpawnRoot.value) tabs.push('orchestration')
    return tabs
})

// Tab visit history for Alt+Shift+↑ (last-visited, Alt+Tab-like behavior).
// Plain array (not reactive) — no template depends on it.
// Persists as long as the component is KeepAlive'd.
const tabHistory = []
const MAX_TAB_HISTORY = 50

function pushTabHistory(tabId) {
    if (tabHistory.length > 0 && tabHistory[tabHistory.length - 1] === tabId) return
    tabHistory.push(tabId)
    if (tabHistory.length > MAX_TAB_HISTORY) tabHistory.shift()
}

// Track tab transitions for history (separate from the store sync watcher).
// oldTabId is undefined on the first call, so we guard with `if (oldTabId)`.
watch(activeTabId, (newTabId, oldTabId) => {
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    if (oldTabId) pushTabHistory(oldTabId)
})

// Direct tab mapping: Alt+Shift+{1..6} → fixed tabs (subagents are skipped).
// Artifacts (5) and Orchestration (6) are conditional — the handler no-ops when
// the tab is absent.
const DIRECT_TAB_MAP = { 1: 'main', 2: 'files', 3: 'git', 4: 'terminal', 5: 'artifacts', 6: 'orchestration' }

// Flag set by keyboard tab navigation to auto-focus the relevant element on tab arrival
let pendingKeyboardFocus = false

/**
 * Handle keyboard tab shortcut events dispatched from App.vue.
 * Only the active SessionView instance processes the event (KeepAlive guard).
 */
function handleTabShortcut(event) {
    if (!isActive.value) return

    const { type, index } = event.detail
    let targetTab = null

    if (type === 'direct') {
        targetTab = DIRECT_TAB_MAP[index]
        if (!targetTab) return
        if (targetTab === 'git' && !hasGitRepo.value) return
        if (targetTab === 'artifacts' && !hasArtifacts.value) return
        if (targetTab === 'orchestration' && !hasSpawnRoot.value) return
    } else if (type === 'prev' || type === 'next') {
        const tabs = orderedTabs.value
        const currentIndex = tabs.indexOf(activeTabId.value)
        if (currentIndex === -1) return
        const newIndex = type === 'next'
            ? (currentIndex + 1) % tabs.length
            : (currentIndex - 1 + tabs.length) % tabs.length
        targetTab = tabs[newIndex]
    } else if (type === 'last-visited') {
        const tabs = orderedTabs.value
        // Walk history backwards to find the most recent tab that still exists
        // and isn't the currently active one
        for (let i = tabHistory.length - 1; i >= 0; i--) {
            const tabId = tabHistory[i]
            if (tabId !== activeTabId.value && tabs.includes(tabId)) {
                targetTab = tabId
                break
            }
        }
    }

    if (!targetTab) return
    pendingKeyboardFocus = true
    switchToTab(targetTab)
}

// ═══════════════════════════════════════════════════════════════════════════
// Drag-hover: spring-loaded tab switching (hover 1s while dragging to switch)
// ═══════════════════════════════════════════════════════════════════════════

// Drag-hover on the Chat tab: switches to it when dragging files/text over it for 1 second.
// If files/text are dropped directly on the tab, forward to SessionItemsList for processing.
const chatTabDragHover = useDragHover({
    onActivate: () => switchToTab('main'),
    shouldActivate: () => activeTabId.value !== 'main',
    onDropData: (data) => {
        // Ensure we're on the Chat tab before forwarding
        if (activeTabId.value !== 'main') {
            switchToTab('main')
        }
        nextTick(() => {
            sessionItemsListRef.value?.handleForwardedDrop(data)
        })
    },
})

// Pick up pending drop data from ProjectView (when files/text were dropped on a session list item).
const pendingDropData = inject('pendingDropData', ref(null))
watch(pendingDropData, (data) => {
    if (!data || data.sessionId !== sessionId.value) return
    // Consume the pending data
    pendingDropData.value = null
    // Ensure we're on the Chat tab
    if (activeTabId.value !== 'main') {
        switchToTab('main')
    }
    nextTick(() => {
        sessionItemsListRef.value?.handleForwardedDrop(data)
    })
})

/**
 * Handle tab change event from wa-tab-group.
 * Updates the URL to reflect the new active tab.
 */
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return
    switchToTab(panel)

    // Auto-focus the chat's primary control (pending request form when active,
    // message input otherwise) when arriving on the chat tab via keyboard navigation.
    if (pendingKeyboardFocus) {
        pendingKeyboardFocus = false
        if (panel === 'main') {
            nextTick(() => focusChatPrimary())
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Compact tab nav: scroll overflow controls
// (mirrors wa-tab-group's native scroll behavior)
// ═══════════════════════════════════════════════════════════════════════════

const compactTabScrollArea = ref(null)
const compactTabsCanScrollStart = ref(false)
const compactTabsCanScrollEnd = ref(false)
const compactTabsHasOverflow = ref(false)
let compactTabsResizeObserver = null

/**
 * Update scroll control visibility based on overflow and current scroll position.
 * - hasOverflow: whether the tab area overflows at all (controls DOM presence)
 * - canScrollStart: whether there is hidden content to the left (controls opacity)
 * - canScrollEnd: whether there is hidden content to the right (controls opacity)
 */
function updateCompactTabsScrollControls() {
    const el = compactTabScrollArea.value
    if (!el) {
        compactTabsHasOverflow.value = false
        compactTabsCanScrollStart.value = false
        compactTabsCanScrollEnd.value = false
        return
    }
    const tolerance = 1 // Same safety margin as wa-tab-group
    compactTabsHasOverflow.value = el.scrollWidth > el.clientWidth + tolerance
    compactTabsCanScrollStart.value = el.scrollLeft > tolerance
    compactTabsCanScrollEnd.value = el.scrollLeft + el.clientWidth < el.scrollWidth - tolerance
}

/**
 * Scroll the compact tabs by one viewport width in the given direction.
 * @param {'start' | 'end'} direction
 */
function scrollCompactTabs(direction) {
    const el = compactTabScrollArea.value
    if (!el) return
    const delta = direction === 'start' ? -el.clientWidth : el.clientWidth
    el.scroll({ left: el.scrollLeft + delta, behavior: 'smooth' })
}

/**
 * Handle native scroll events on the compact tab area to update arrow visibility.
 */
function onCompactTabsScroll() {
    updateCompactTabsScrollControls()
}

// Start/stop the ResizeObserver + scroll listener with KeepAlive lifecycle
function startCompactTabsObserver() {
    nextTick(() => {
        const el = compactTabScrollArea.value
        if (!el) return
        updateCompactTabsScrollControls()
        el.addEventListener('scroll', onCompactTabsScroll, { passive: true })
        compactTabsResizeObserver = new ResizeObserver(() => updateCompactTabsScrollControls())
        compactTabsResizeObserver.observe(el)
    })
}

function stopCompactTabsObserver() {
    compactTabScrollArea.value?.removeEventListener('scroll', onCompactTabsScroll)
    if (compactTabsResizeObserver) {
        compactTabsResizeObserver.disconnect()
        compactTabsResizeObserver = null
    }
}

// Recalculate scroll controls when the number of tabs changes
watch(openSubagentTabs, () => {
    nextTick(() => updateCompactTabsScrollControls())
})

/**
 * Close a subagent tab.
 * @param {string} tabId - The tab ID to close (e.g., 'agent-xxx')
 */
function closeTab(tabId) {
    const tabs = openSubagentTabs.value
    const index = tabs.findIndex(t => t.id === tabId)
    if (index === -1) return

    // Remove the tab from store
    store.removeSessionTab(sessionId.value, tabId)

    // If this was the active tab, navigate to the tab on the left
    if (activeTabId.value === tabId) {
        if (index > 0) {
            // Go to the previous subagent tab (use current tabs, not yet updated)
            const prevTab = tabs[index - 1]
            router.push({
                name: buildSubagentRouteName(isAllProjectsMode.value),
                params: {
                    projectId: filterProjectId.value,
                    sessionId: sessionId.value,
                    subagentId: prevTab.agentId
                },
                query: route.query,
            })
        } else {
            // No more subagent tabs, go to main
            router.push({
                name: buildSessionBaseRouteName(isAllProjectsMode.value),
                params: {
                    projectId: filterProjectId.value,
                    sessionId: sessionId.value
                },
                query: route.query,
            })
        }
    }
}

/**
 * Open a subagent tab if not already open.
 * @param {string} agentId - The agent ID
 */
function openSubagentTab(agentId) {
    store.addSessionTab(sessionId.value, `agent-${agentId}`)
}

/**
 * Label rendered in the subagent tab buttons (compact dropdown, tab
 * bar, wa-tabs nav). Prefers ``Session.slug`` when the provider exposes
 * one (Codex stores the agent_nickname there); falls back to the first
 * 8 characters of the agent id otherwise (Claude Code, where slug
 * is currently unset).
 */
function getAgentTabLabel(agentId) {
    return getAgentDisplayLabel(agentId, store)
}

// Watch subagentId to open tab when navigating to a subagent URL.
// Two guards prevent incorrect tab additions with KeepAlive (same logic as activeTabId watcher):
// 1. isActive: skip when deactivated — don't react to route changes while cached
// 2. sessionId check: skip when the route belongs to a different session
watch(subagentId, (newSubagentId) => {
    if (!newSubagentId) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    openSubagentTab(newSubagentId)
}, { immediate: true })

// Sync active tab in store when the route changes for THIS session.
watch(activeTabId, (newTabId) => {
    if (!sessionId.value) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    store.setSessionActiveTab(sessionId.value, newTabId)

}, { immediate: true })

/**
 * Handle a session that needs a title after sending its first message.
 * If title auto-apply is enabled, requests a suggestion and applies it
 * automatically when it arrives (same flow as the rename dialog's Save).
 * Otherwise, opens the rename dialog.
 */
function handleNeedsTitle() {
    if (settingsStore.isTitleAutoApply && settingsStore.isTitleGenerationEnabled) {
        const sid = sessionId.value
        const pid = projectId.value
        const prompt = store.getDraftMessage(sid)?.message?.trim()
        if (!prompt) return

        // Register the intent in the store BEFORE firing the WS request so
        // the global auto-apply watcher (set up in main.js via
        // ``startAutoApplyTitleWatcher``) is already observing this session
        // even if the backend reply comes back in the same tick. The
        // watcher lives at module scope and survives the router.replace
        // that ``bindDraftSession`` performs for Codex drafts.
        store.registerPendingTitleAutoApply(sid, pid)
        requestTitleSuggestion(sid, prompt, settingsStore.getTitleSystemPrompt)
    } else {
        sessionHeaderRef.value?.openRenameDialog({ showHint: true })
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Command palette: contextual session commands
// ═══════════════════════════════════════════════════════════════════════════

const SESSION_COMMAND_IDS = [
    'session.rename',
    'session.archive',
    'session.unarchive',
    'session.pin-mode',
    'session.mark-read',
    'session.mark-unread',
    'session.stop',
    'session.delete-draft',
    'session.focus-input',
    'session.collapse-input',
    'session.expand-input',
    'session.model',
    'session.effort',
    'session.permission',
    'session.thinking',
    'session.context',
    'session.chrome',
    'session.fast-mode',
]

// Read/unread gate for the current session, mirroring SessionListItem's
// canToggleReadState + hasUnread — minus the "is this the active row" guard,
// since here the session IS the one on screen. Returns `{ unread }` (the raw
// unread flag), or null when toggling read state isn't allowed (draft, or a
// process running outside user_turn).
function currentSessionReadState() {
    const s = store.getSession(sessionId.value)
    if (!s || s.draft) return null
    const ps = store.getProcessState(sessionId.value)
    if (ps && ps.state !== PROCESS_STATE.USER_TURN) return null
    const unread = !!s.last_new_content_at
        && (!s.last_viewed_at || s.last_new_content_at > s.last_viewed_at)
    return { unread }
}

function registerSessionCommands() {
    registerCommands([
        {
            id: 'session.rename',
            label: 'Rename Session',
            icon: 'pencil',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            action: () => sessionHeaderRef.value?.openRenameDialog(),
        },
        {
            id: 'session.archive',
            label: 'Archive Session',
            icon: 'box-archive',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft && !s.archived
            },
            action: () => stopSessionProcess(sessionId.value, { archive: true }),
        },
        {
            id: 'session.unarchive',
            label: 'Unarchive Session',
            icon: 'box-open',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.archived
            },
            action: () => store.setSessionArchived(projectId.value, sessionId.value, false),
        },
        {
            id: 'session.pin-mode',
            label: 'Change Pin Mode…',
            icon: 'thumbtack',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            items: () => {
                const s = store.getSession(sessionId.value)
                const current = s?.pinned ?? null
                const pick = (mode) => store.setSessionPinMode(projectId.value, sessionId.value, mode)
                return [
                    { id: 'none',      label: 'Not pinned',   action: () => pick(null),        active: !current },
                    { id: 'project',   label: 'Project',      action: () => pick('project'),   active: current === 'project' },
                    { id: 'workspace', label: 'Workspace',    action: () => pick('workspace'), active: current === 'workspace' },
                    { id: 'all',       label: 'Everywhere', action: () => pick('all'),       active: current === 'all' },
                ]
            },
        },
        {
            id: 'session.mark-read',
            label: 'Mark as Read',
            icon: 'eye-slash',
            category: 'session',
            when: () => currentSessionReadState()?.unread === true,
            action: () => markSessionReadState(sessionId.value, false),
        },
        {
            id: 'session.mark-unread',
            label: 'Mark as Unread',
            icon: 'eye',
            category: 'session',
            when: () => currentSessionReadState()?.unread === false,
            action: () => {
                // Cancel any pending session_viewed throttle so it can't re-mark
                // this session read, flag it unread, then leave it — staying on
                // the session would reset it to read (mirrors SessionListItem's
                // mark-unread on the active row).
                cancelSessionViewedThrottle(sessionId.value)
                markSessionReadState(sessionId.value, true)
                if (isAllProjectsMode.value) {
                    router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
                } else {
                    router.push({ name: 'project', params: { projectId: filterProjectId.value } })
                }
            },
        },
        {
            id: 'session.stop',
            label: 'Stop Process',
            icon: 'stop',
            category: 'session',
            when: () => {
                const ps = store.getProcessState(sessionId.value)
                return !!ps && ps.state !== PROCESS_STATE.DEAD && !ps.synthetic
            },
            action: () => stopSessionProcess(sessionId.value),
        },
        {
            id: 'session.delete-draft',
            label: 'Delete Draft',
            icon: 'trash',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.draft
            },
            action: () => {
                store.deleteDraftSession(sessionId.value)
                if (isAllProjectsMode.value) {
                    router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
                } else {
                    router.push({ name: 'project', params: { projectId: filterProjectId.value } })
                }
            },
        },
        {
            id: 'session.focus-input',
            label: 'Focus Message Input',
            icon: 'keyboard',
            category: 'session',
            // Direct access to the message input specifically — opens it in the
            // footer accordion (reducing the terminal / pending request) and
            // focuses the textarea. Distinct from Alt+Shift+M, which lands on the
            // pending request form instead when one is open. Mirrors Alt+Shift+PageDown.
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-message-input'),
        },
        {
            id: 'session.focus-pending',
            label: 'Focus Pending Request',
            icon: 'reply',
            category: 'session',
            // Only when an answerable pending request form is shown (a request
            // degraded to badge-only — hybrid_terminal — has no form to focus).
            when: () => {
                const reqs = store.getPendingRequests(sessionId.value)
                return reqs.length > 0 && reqs[0].request_type !== 'hybrid_terminal'
            },
            // Opens the pending request in the accordion (reducing the others) and
            // focuses its primary control. Mirrors Alt+Shift+PageUp.
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-pending-request'),
        },
        {
            id: 'session.focus-terminal',
            label: 'Open Claude CLI Terminal',
            icon: 'terminal',
            category: 'session',
            // Hybrid sessions only — the embedded CLI terminal block. Hidden
            // entirely while the hybrid feature flag is off.
            when: () => settingsStore.isClaudeHybridEnabled && store.getSession(sessionId.value)?.hybrid === true,
            // Opens the terminal in the accordion (reducing the others) and
            // focuses the xterm. (Alt+Shift+T toggles instead; this only opens.)
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-terminal'),
        },
        {
            id: 'session.toggle-hybrid',
            label: 'Toggle Hybrid Mode',
            icon: 'right-left',
            category: 'session',
            // Claude sessions where hybrid is actually toggleable — not a
            // committed-permanent one (its switch is one-way). Drafts, staged
            // SDK sessions, and plain non-hybrid SDK sessions all qualify.
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s
                    && settingsStore.isClaudeHybridEnabled
                    && s.provider === 'claude_code'
                    && !s.hidden
                    && !s.parent_session_id
                    && !(!s.draft && s.hybrid === true)
            },
            // Same as clicking the composer's hybrid button (enable/disable a
            // draft, stage/un-stage, or open the confirm dialog). Mirrors Alt+Shift+H.
            action: () => gotoChatFooterPanel(route, router, 'twicc:toggle-hybrid'),
        },
        {
            id: 'session.collapse-input',
            label: 'Collapse Message Input',
            icon: 'chevron-down',
            category: 'session',
            // Only when a message input is actually shown (main session, not
            // stale/disabled) and currently expanded — including while it sits
            // next to a pending request (the composer collapses independently,
            // leaving the request as-is). We probe the DOM because the collapsed
            // state is local to MessageInput; when() is re-evaluated each palette open.
            when: () => !!document.querySelector('.message-input:not(.collapsed)'),
            action: () => document
                .querySelector('.message-input:not(.collapsed)')
                ?.dispatchEvent(new CustomEvent('twicc:collapse-composer')),
        },
        {
            id: 'session.expand-input',
            label: 'Expand Message Input',
            icon: 'chevron-up',
            category: 'session',
            when: () => !!document.querySelector('.message-input.collapsed'),
            // Expand the composer directly (the same twicc:expand-composer event).
            // We can't route through focusChatPrimary anymore: a collapsed composer
            // now coexists with a pending request, and focusChatPrimary would focus
            // the request instead of expanding the composer. MessageInput focuses
            // the textarea itself once expanded, and expanding it reduces the
            // request (at most one of the two is expanded at a time).
            action: () => document
                .querySelector('.message-input.collapsed')
                ?.dispatchEvent(new CustomEvent('twicc:expand-composer')),
        },
        ...buildSessionSettingsCommands(),
    ])
}

// ─── Session settings commands (mirror of MessageInput settings popover) ────

function sessionSettingsGate() {
    const s = store.getSession(sessionId.value)
    if (!s) return null
    const gate = sessionItemsListRef.value?.getSessionGateState()
    if (!gate) return null
    if (gate.isStarting) return null
    return gate
}

function getSessionSettingValue(key) {
    return sessionItemsListRef.value?.getSessionSetting(key) ?? null
}

function setSessionSettingValue(key, value) {
    sessionItemsListRef.value?.setSessionSetting(key, value)
}

function buildSessionSettingsCommands() {
    const provider = session.value?.provider
    const helpers = getProviderHelpers(provider)
    if (!helpers) return []

    const isAvailable = () => !!sessionSettingsGate()

    function buildDefaultItem(field, current) {
        return {
            id: '__default__',
            group: 'default',
            label: `Default: ${helpers.getDefaultValueLabel(field, helpers.getDefaultValue(field))}`,
            action: () => setSessionSettingValue(field, null),
            active: current === null,
        }
    }

    function buildSimpleCommand(field, { id, label, icon, when }) {
        if (!helpers.supportsAgentSetting(field)) return []
        return [{
            id,
            label,
            icon,
            category: 'session',
            when: when ?? isAvailable,
            items: () => {
                const gate = sessionSettingsGate()
                if (!gate) return []
                const current = getSessionSettingValue(field)
                const items = [buildDefaultItem(field, current)]
                for (const choice of helpers.getFieldChoices(field)) {
                    if (helpers.isChoiceDisabled(field, choice.value, gate)) continue
                    items.push({
                        id: String(choice.value),
                        group: 'force',
                        label: choice.label,
                        action: () => setSessionSettingValue(field, choice.value),
                        active: current === choice.value,
                    })
                }
                return items
            },
        }]
    }

    return [
        ...(helpers.supportsAgentSetting('selected_model') ? [{
            id: 'session.model',
            label: 'Change Session Model…',
            icon: 'robot',
            category: 'session',
            when: isAvailable,
            items: () => {
                const current = getSessionSettingValue('selected_model')
                const items = [buildDefaultItem('selected_model', current)]
                const groups = helpers.getModelSelectGroups(helpers.getModelRegistry?.() ?? [])
                groups.forEach((group, idx) => {
                    const groupKey = `model_group_${idx}`
                    for (const entry of group.entries ?? []) {
                        if (entry.disabled) continue
                        items.push({
                            id: entry.value,
                            group: groupKey,
                            label: entry.label,
                            action: () => setSessionSettingValue('selected_model', entry.value),
                            active: current === entry.value,
                        })
                    }
                })
                return items
            },
        }] : []),
        ...buildSimpleCommand('effort', {
            id: 'session.effort',
            label: 'Change Session Effort…',
            icon: 'gauge',
        }),
        ...buildSimpleCommand('thinking_enabled', {
            id: 'session.thinking',
            label: 'Change Session Thinking…',
            icon: 'brain',
        }),
        ...buildSimpleCommand('permission_mode', {
            id: 'session.permission',
            label: 'Change Session Permission Mode…',
            icon: 'shield-halved',
        }),
        ...buildSimpleCommand('context_max', {
            id: 'session.context',
            label: 'Change Session Context Size…',
            icon: 'window-maximize',
            when: () => {
                const gate = sessionSettingsGate()
                if (!gate) return false
                return !gate.isContextMaxForced && !gate.isContextMaxForcedByModel
            },
        }),
        ...buildSimpleCommand('claude_in_chrome', {
            id: 'session.chrome',
            label: 'Change Session Claude in Chrome MCP…',
            icon: 'globe',
        }),
        ...buildSimpleCommand('fast_mode', {
            id: 'session.fast-mode',
            label: 'Change Session Fast Mode…',
            icon: 'gauge-high',
        }),
    ]
}

onBeforeUnmount(() => {
    unregisterCommands(SESSION_COMMAND_IDS)
    chatTabDragHover.cancel()
})
</script>

<template>
    <div class="session-view">
        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            ref="sessionHeaderRef"
            :session-id="sessionId"
            mode="session"
            :tabs="compactTabs"
            :active-tab-id="activeTabId"
            @select-tab="switchToTab"
        >
            <!-- Compact mode: tab navigation inside the header overlay -->
            <template #compact-extra>
                <div class="compact-tab-nav" :class="{ 'has-scroll-controls': compactTabsHasOverflow }">
                    <!-- Scroll left button (faded when at the start) -->
                    <wa-button
                        v-if="compactTabsHasOverflow"
                        class="compact-tab-scroll compact-tab-scroll-start"
                        :class="{ 'scroll-disabled': !compactTabsCanScrollStart }"
                        appearance="plain"
                        size="small"
                        :disabled="!compactTabsCanScrollStart"
                        @click="scrollCompactTabs('start')"
                    >
                        <wa-icon name="chevron-left" variant="solid" label="Scroll left"></wa-icon>
                    </wa-button>

                    <!-- Scrollable tabs container -->
                    <div class="compact-tab-scroll-area" ref="compactTabScrollArea">
                        <wa-button
                            :appearance="activeTabId === 'main' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'main' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('main')"
                            @dragenter="chatTabDragHover.onDragenter"
                            @dragleave="chatTabDragHover.onDragleave"
                            @dragover="chatTabDragHover.onDragover"
                            @drop="chatTabDragHover.onDrop"
                            :class="{ 'drag-hover-pending': chatTabDragHover.isPending.value }"
                        >
                            Chat
                            <CodeCommentsIndicator slot="end" :count="chatCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                            <wa-icon
                                v-if="store.getPendingRequests(sessionId).length > 0"
                                slot="end"
                                name="hand"
                                class="pending-request-indicator"
                            ></wa-icon>
                        </wa-button>

                        <wa-button
                            v-for="tab in openSubagentTabs"
                            :key="tab.id"
                            :appearance="activeTabId === tab.id ? 'outlined' : 'plain'"
                            :variant="activeTabId === tab.id ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse(tab.id)"
                        >
                            <span class="subagent-tab-content">
                                <span>Agent "{{ getAgentTabLabel(tab.agentId) }}"</span>
                                <ProcessIndicator
                                    v-if="store.getProcessState(tab.agentId)"
                                    :state="store.getProcessState(tab.agentId).state"
                                    size="small"
                                />
                                <CodeCommentsIndicator :count="agentCommentsCount(tab.agentId)" :show-tooltip="false" class="tab-comments-indicator" />
                                <span class="tab-close-icon" @click.stop="closeTab(tab.id)">
                                    <wa-icon name="xmark" label="Close tab"></wa-icon>
                                </span>
                            </span>
                        </wa-button>

                        <wa-button
                            :appearance="activeTabId === 'files' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'files' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('files')"
                        >
                            Files
                            <CodeCommentsIndicator slot="end" :count="filesCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                        </wa-button>

                        <wa-button
                            v-if="hasGitRepo"
                            :appearance="activeTabId === 'git' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'git' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('git')"
                        >
                            Git
                            <CodeCommentsIndicator slot="end" :count="gitCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                        </wa-button>

                        <wa-button
                            :appearance="activeTabId === 'terminal' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'terminal' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('terminal')"
                        >Terminal</wa-button>

                        <wa-button
                            v-if="hasArtifacts"
                            :appearance="activeTabId === 'artifacts' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'artifacts' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('artifacts')"
                        >Artifacts</wa-button>

                        <wa-button
                            v-if="hasSpawnRoot"
                            :appearance="activeTabId === 'orchestration' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'orchestration' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('orchestration')"
                        >Orchestration</wa-button>
                    </div>

                    <!-- Scroll right button (faded when at the end) -->
                    <wa-button
                        v-if="compactTabsHasOverflow"
                        class="compact-tab-scroll compact-tab-scroll-end"
                        :class="{ 'scroll-disabled': !compactTabsCanScrollEnd }"
                        appearance="plain"
                        size="small"
                        :disabled="!compactTabsCanScrollEnd"
                        @click="scrollCompactTabs('end')"
                    >
                        <wa-icon name="chevron-right" variant="solid" label="Scroll right"></wa-icon>
                    </wa-button>
                </div>
            </template>
        </SessionHeader>

        <SessionLayout
            v-if="session"
            ref="sessionLayoutRef"
            :layout="layout"
            :register-target="registerLayoutTarget"
            :unregister-target="unregisterLayoutTarget"
            @select-tab="switchToTab"
            @minimize="onLayoutMinimize"
        >
        <wa-tab-group
            :active="centerActiveTab"
            @wa-tab-show="onTabShow"
            @pointerdown.capture="onCenterPointerDown"
            class="session-tabs"
        >
            <!-- Tab navigation -->
            <wa-tab slot="nav" panel="main"
                @dragenter="chatTabDragHover.onDragenter"
                @dragleave="chatTabDragHover.onDragleave"
                @dragover="chatTabDragHover.onDragover"
                @drop="chatTabDragHover.onDrop"
                :class="{ 'drag-hover-pending': chatTabDragHover.isPending.value }"
            >
                <wa-button
                    :appearance="centerActiveTab === 'main' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'main' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Chat
                    <CodeCommentsIndicator slot="end" :count="chatCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                    <wa-icon
                        v-if="store.getPendingRequests(sessionId).length > 0"
                        slot="end"
                        :id="`session-tab-chat-${sessionId}-pending-request`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                </wa-button>
                <AppTooltip v-if="store.getPendingRequests(sessionId).length > 0" :for="`session-tab-chat-${sessionId}-pending-request`">Waiting for your response</AppTooltip>
            </wa-tab>

            <!-- Subagent tabs with close button -->
            <template v-for="tab in openSubagentTabs" :key="tab.id">
                <wa-tab slot="nav" :panel="tab.id">
                    <wa-button
                        :appearance="centerActiveTab === tab.id ? 'outlined' : 'plain'"
                        :variant="centerActiveTab === tab.id ? 'brand' : 'neutral'"
                        size="small"
                    >
                        <span class="subagent-tab-content">
                            <span>Agent "{{ getAgentTabLabel(tab.agentId) }}"</span>
                            <ProcessIndicator
                                v-if="store.getProcessState(tab.agentId)"
                                :state="store.getProcessState(tab.agentId).state"
                                size="small"
                            />
                            <CodeCommentsIndicator :count="agentCommentsCount(tab.agentId)" :show-tooltip="false" class="tab-comments-indicator" />
                            <span class="tab-close-icon" @click.stop="closeTab(tab.id)">
                                <wa-icon name="xmark" label="Close tab"></wa-icon>
                            </span>
                        </span>
                    </wa-button>
                </wa-tab>
            </template>

            <!-- Tool tabs — shown in the center strip unless docked; arrow places them -->
            <wa-tab v-if="showInCenter('files')" slot="nav" panel="files">
                <wa-button
                    :appearance="centerActiveTab === 'files' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'files' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Files
                    <CodeCommentsIndicator slot="end" :count="filesCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                </wa-button>
                <TabPlacementMenu tab-id="files" current="center" @place="(dest) => layout.place('files', dest)" />
            </wa-tab>
            <wa-tab v-if="hasGitRepo && showInCenter('git')" slot="nav" panel="git">
                <wa-button
                    :appearance="centerActiveTab === 'git' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'git' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Git
                    <CodeCommentsIndicator slot="end" :count="gitCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                </wa-button>
                <TabPlacementMenu tab-id="git" current="center" @place="(dest) => layout.place('git', dest)" />
            </wa-tab>
            <wa-tab v-if="showInCenter('terminal')" slot="nav" panel="terminal">
                <wa-button
                    :appearance="centerActiveTab === 'terminal' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'terminal' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Terminal
                </wa-button>
                <TabPlacementMenu tab-id="terminal" current="center" @place="(dest) => layout.place('terminal', dest)" />
            </wa-tab>
            <wa-tab v-if="hasArtifacts && showInCenter('artifacts')" slot="nav" panel="artifacts">
                <wa-button
                    :appearance="centerActiveTab === 'artifacts' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'artifacts' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Artifacts
                </wa-button>
                <TabPlacementMenu tab-id="artifacts" current="center" @place="(dest) => layout.place('artifacts', dest)" />
            </wa-tab>
            <wa-tab v-if="hasSpawnRoot && showInCenter('orchestration')" slot="nav" panel="orchestration">
                <wa-button
                    :appearance="centerActiveTab === 'orchestration' ? 'outlined' : 'plain'"
                    :variant="centerActiveTab === 'orchestration' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Orchestration
                </wa-button>
                <TabPlacementMenu tab-id="orchestration" current="center" @place="(dest) => layout.place('orchestration', dest)" />
            </wa-tab>

            <!-- Main session panel -->
            <wa-tab-panel name="main">
                <SessionItemsList
                    ref="sessionItemsListRef"
                    :session-id="sessionId"
                    :project-id="projectId"
                    @needs-title="handleNeedsTitle"
                />
            </wa-tab-panel>

            <!-- Subagent panels -->
            <wa-tab-panel
                v-for="tab in openSubagentTabs"
                :key="tab.id"
                :name="tab.id"
            >
                <SessionContent
                    :session-id="tab.agentId"
                    :parent-session-id="sessionId"
                    :project-id="projectId"
                />
            </wa-tab-panel>

            <!-- Tool panels live in the host below (teleported); here are only their center targets -->
            <wa-tab-panel v-if="showInCenter('files')" name="files">
                <div :ref="centerTargetSetters.files" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="hasGitRepo && showInCenter('git')" name="git">
                <div :ref="centerTargetSetters.git" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="showInCenter('terminal')" name="terminal">
                <div :ref="centerTargetSetters.terminal" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="hasArtifacts && showInCenter('artifacts')" name="artifacts">
                <div :ref="centerTargetSetters.artifacts" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="hasSpawnRoot && showInCenter('orchestration')" name="orchestration">
                <div :ref="centerTargetSetters.orchestration" class="layout-center-target"></div>
            </wa-tab-panel>
        </wa-tab-group>
        </SessionLayout>

        <!-- Session not found (backend returned 404) -->
        <div v-else-if="sessionLoadError === 'not-found'" class="empty-state">
            <wa-callout variant="warning" size="small">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                Session not found
            </wa-callout>
        </div>

        <!-- Session load failed (network / server error) -->
        <div v-else-if="sessionLoadError === 'error'" class="empty-state">
            <wa-callout variant="danger" size="small">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                Failed to load session
            </wa-callout>
        </div>

        <!-- Loading state -->
        <div v-else class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading session...</span>
        </div>

        <!-- Tool panels: mounted once here, teleported to their center slot, dock region, or overlay.
             Moving a tab between docks just retargets its Teleport — the instance is never re-mounted. -->
        <div v-if="session" class="layout-panel-host" aria-hidden="true">
            <Teleport :to="toolTarget('files')" :disabled="!toolTarget('files')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('files')">
                    <FilesPanel
                        ref="filesPanelRef"
                        :project-id="session?.project_id"
                        :session-id="session?.id"
                        :git-directory="session?.git_directory"
                        :session-cwd="session?.cwd"
                        :project-git-root="store.getProject(session?.project_id)?.git_root"
                        :project-directory="store.getProject(session?.project_id)?.directory"
                        :route-root-key="activeTabId === 'files' ? filesRouteRootKey : undefined"
                        :route-file-path="activeTabId === 'files' ? filesRouteFilePath : undefined"
                        :active="isActive && isToolTabShown('files')"
                        :is-draft="session?.draft === true"
                        @navigate="onFilesNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="hasGitRepo" :to="toolTarget('git')" :disabled="!toolTarget('git')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('git')">
                    <GitPanel
                        ref="gitPanelRef"
                        :project-id="session?.project_id"
                        :session-id="session?.id"
                        :git-directory="session?.git_directory"
                        :project-git-root="store.getProject(session?.project_id)?.git_root"
                        :initial-branch="session?.git_branch || ''"
                        :route-root-key="activeTabId === 'git' ? gitRouteRootKey : undefined"
                        :route-commit-ref="activeTabId === 'git' ? gitRouteCommitRef : undefined"
                        :route-file-path="activeTabId === 'git' ? gitRouteFilePath : undefined"
                        :active="isActive && isToolTabShown('git')"
                        :is-draft="session?.draft === true"
                        @navigate="onGitNavigate"
                    />
                </div>
            </Teleport>

            <Teleport :to="toolTarget('terminal')" :disabled="!toolTarget('terminal')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('terminal')">
                    <TerminalPanel
                        ref="terminalPanelRef"
                        :context-key="`s:${session.id}`"
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :route-term-index="activeTabId === 'terminal' ? terminalRouteTermIndex : undefined"
                        :active="isActive && isToolTabShown('terminal')"
                        @navigate="onTerminalNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="hasArtifacts" :to="toolTarget('artifacts')" :disabled="!toolTarget('artifacts')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('artifacts')">
                    <FilesPanel
                        ref="artifactsPanelRef"
                        :project-id="null"
                        :session-id="null"
                        :api-prefix="'/api'"
                        :external-roots="artifactsExternalRoots"
                        :root-restriction="artifactsDir"
                        :show-root-selector="false"
                        root-label="Artifacts"
                        :preview-by-default="true"
                        :artifact-bookmark-session-id="session?.id"
                        :route-root-key="activeTabId === 'artifacts' ? artifactsRouteRootKey : undefined"
                        :route-file-path="activeTabId === 'artifacts' ? artifactsRouteFilePath : undefined"
                        :active="isActive && isToolTabShown('artifacts')"
                        @navigate="onArtifactsNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="hasSpawnRoot" :to="toolTarget('orchestration')" :disabled="!toolTarget('orchestration')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('orchestration')">
                    <OrchestrationPanel
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :active="isActive && isToolTabShown('orchestration')"
                    />
                </div>
            </Teleport>
        </div>
    </div>
</template>

<style scoped>
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    position: relative;
}

.session-view > wa-divider {
    flex-shrink: 0;
}

/* Dockable layout: panels are mounted once in this hidden host, then teleported into the
   center tab-panel / a dock region / the overlay. While here (no target) they stay mounted. */
.layout-panel-host {
    display: none;
}
.layout-center-target,
.layout-tool-wrap {
    flex: 1;
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tab group styles
   ═══════════════════════════════════════════════════════════════════════════ */

.session-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    --indicator-color: transparent;
    --track-width: var(--divider-size);
}

.session-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}

.session-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.session-tabs :deep(wa-tab-panel::part(base)) {
    padding: 0;
}

wa-tab::part(base) {
    padding: var(--wa-space-xs);
}

/* Active tab panel needs to fill available space and handle overflow */
.session-tabs :deep(wa-tab-panel[active]) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.session-tabs :deep(wa-tab-panel[active])::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* Subagent tab content wrapper */
.subagent-tab-content {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.tab-close-icon {
    aspect-ratio: 1;
    height: 3em;
    margin-right: -1em;
    width: auto;
    font-size: 0.75rem;
    opacity: 0.5;
    cursor: pointer;
    transition: opacity 0.15s ease;
    display: grid;
    place-items: center;
}

.tab-close-icon:hover {
    opacity: 1;
}

.tab-comments-indicator {
    font-size: var(--wa-font-size-xs);
    flex-shrink: 0;
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
   Empty state
   ═══════════════════════════════════════════════════════════════════════════ */

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

/* ═══════════════════════════════════════════════════════════════════════════
   Compact mode: tab nav inside header overlay
   ═══════════════════════════════════════════════════════════════════════════ */

/* Hidden by default on large viewports */
.compact-tab-nav {
    display: none;
}

@media (max-height: 900px) {
    /* Hide the real tab-group nav in compact mode */
    .session-tabs::part(nav) {
        display: none;
    }

    /* Show the compact tab nav inside the header overlay */
    .compact-tab-nav {
        display: flex;
        align-items: center;
        position: relative;
        padding-inline: var(--wa-space-xs);
        padding-bottom: var(--wa-space-xs);
    }

    /* When overflowing, add padding on both sides for the scroll arrows */
    .compact-tab-nav.has-scroll-controls {
        padding-inline: calc(var(--wa-space-xs) + 1.5em);
    }

    /* Scrollable area: horizontal scroll with hidden scrollbar */
    .compact-tab-scroll-area {
        display: flex;
        gap: var(--wa-space-2xs);
        overflow-x: auto;
        scrollbar-width: none; /* Firefox */
        flex: 1;
        min-width: 0;
    }

    .compact-tab-scroll-area::-webkit-scrollbar {
        height: 0; /* Chrome/Safari */
    }

    /* Prevent tabs from shrinking */
    .compact-tab-scroll-area > wa-button {
        flex-shrink: 0;
    }

    /* Scroll arrow buttons — same style as wa-tab-group */
    .compact-tab-scroll {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 1.5em;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1;
        transition: opacity 0.15s ease;
    }

    .compact-tab-scroll.scroll-disabled {
        opacity: 0;
        pointer-events: none;
    }

    .compact-tab-scroll-start {
        left: var(--wa-space-xs);
    }

    .compact-tab-scroll-end {
        right: var(--wa-space-xs);
    }
}
</style>
