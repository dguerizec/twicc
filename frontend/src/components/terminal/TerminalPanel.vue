<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useTerminalConfigStore } from '../../stores/terminalConfig'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useTerminalTabsStore } from '../../stores/terminalTabs'
import { useTerminalCommandStore } from '../../stores/terminalCommand'
import { sendWsMessage } from '../../composables/useWebSocket'
import { toast } from '../../composables/useToast'
import { getUnavailablePlaceholders } from '../../utils/snippetPlaceholders'
import AppTooltip from '../ui/AppTooltip.vue'
import TerminalInstance from './TerminalInstance.vue'
import TerminalRenameDialog from './TerminalRenameDialog.vue'
import TerminalExtraKeysBar from './TerminalExtraKeysBar.vue'
import TerminalCombosDialog from './TerminalCombosDialog.vue'
import TerminalSnippetsDialog from './TerminalSnippetsDialog.vue'
import TerminalSnippetSendDialog from './TerminalSnippetSendDialog.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'

const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
    routeTermIndex: {
        default: undefined,
    },
    // Whether this panel currently owns the URL (it is the focused tab). When the
    // dockable layout shows several tool panels at once, only the focused one owns
    // the route; non-owners receive a blanked routeTermIndex. Route reconciliation
    // must run for the owner ONLY — a non-owner would read the blank index as
    // "go to term 0" and reset the visible terminal back to Main. Defaults to true
    // (non-docked: a panel is shown only when active, so it always owns the route).
    routeOwner: {
        type: Boolean,
        default: true,
    },
})
const emit = defineEmits(['navigate'])

const route = useRoute()
const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const terminalConfigStore = useTerminalConfigStore()
const workspacesStore = useWorkspacesStore()
const terminalTabsStore = useTerminalTabsStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)

const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const resolvedProjectId = computed(() => props.projectId || session.value?.project_id)
// Project id used to SELECT which snippet lists to show: in a worktree session
// we borrow the main repository's workspaces and project-scoped snippets (a
// worktree has none of its own). Placeholders are still resolved against the
// real terminal project (see `buildPlaceholderContext`), not this one.
const snippetListProjectId = computed(() => dataStore.getMainRepoProjectId(resolvedProjectId.value))

// Build a placeholder resolution context for a given snippet.
// For snippets scoped to a project (scope "project:<id>"), use THAT project's data
// for project-related placeholders — even in workspace terminals showing snippets
// from multiple projects. Session is always from props (null for non-session terminals).
function buildPlaceholderContext(snippet) {
    const s = session.value
    // Project used to resolve {project-*} placeholders:
    // - Session/project terminal: always the terminal's own project (e.g. the
    //   worktree itself), even though the displayed snippets may be borrowed
    //   from the main repository's scope.
    // - Workspace/global terminal (no single project): each project-scoped
    //   snippet resolves against its own scope project.
    let pid = resolvedProjectId.value
    if (!pid && snippet?._scope?.startsWith('project:')) {
        pid = snippet._scope.slice('project:'.length)
    }
    const project = pid ? dataStore.getProject(pid) : null
    const projectName = pid ? dataStore.getProjectDisplayName(pid) : null
    return { session: s, project, projectName }
}

// Default context (no snippet-specific project) — used by the send dialog.
const placeholderContext = computed(() => buildPlaceholderContext(null))

// Workspace IDs for snippet scoping:
// - Session/project terminal in a workspace URL: use that workspace
// - Session/project terminal outside workspace: all workspaces containing the project
// - Workspace terminal: use that workspace (extracted from contextKey)
// - Global terminal: empty (global snippets only)
const snippetWorkspaceIds = computed(() => {
    const wsId = route.query.workspace
    if (wsId) return [wsId]
    // Workspace terminal: contextKey is "w:<workspaceId>"
    if (props.contextKey.startsWith('w:')) {
        return [props.contextKey.slice(2)]
    }
    const pid = snippetListProjectId.value
    if (!pid) return []
    return workspacesStore.getWorkspacesForProject(pid).map(ws => ws.id)
})

// Snippets available in current context, with placeholder availability checks.
// For workspace terminals (no single project), get snippets from all workspace projects.
const snippetsForProject = computed(() => {
    let raw
    if (resolvedProjectId.value) {
        // Session or project terminal: snippets for that project (the main repo
        // when the terminal's project is a git worktree).
        raw = terminalConfigStore.getSnippetsForProject(snippetListProjectId.value, snippetWorkspaceIds.value)
    } else if (props.contextKey.startsWith('w:')) {
        // Workspace terminal: merge snippets from all projects in the workspace
        const wsId = props.contextKey.slice(2)
        const projectIds = workspacesStore.getVisibleProjectIds(wsId) || []
        raw = terminalConfigStore.getSnippetsForWorkspace(projectIds, snippetWorkspaceIds.value)
    } else {
        // Global terminal: global snippets only
        raw = terminalConfigStore.getGlobalSnippets()
    }

    return raw.map(snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        // Per-snippet context: project-scoped snippets resolve {project-dir} etc.
        // using their own project's data, not the terminal's project.
        const ctx = buildPlaceholderContext(snippet)
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})

// Whether this session uses tmux (determines if rename is persisted to backend)
const usesTmux = computed(() => {
    if (!settingsStore.isTerminalUseTmux) return false
    const s = session.value
    if (s?.draft || s?.archived) return false
    return true
})

// Dialog refs
const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)
const snippetSendDialogRef = ref(null)
const renameDialogRef = ref(null)

// Terminal instance registration (provide/inject for toolbar + ExtraKeysBar routing)
const terminalApis = reactive(new Map())
provide('registerTerminal', (index, api) => { terminalApis.set(index, api) })
provide('unregisterTerminal', (index) => { terminalApis.delete(index) })

const INVALID_ROUTE_TERM_INDEX = -1

// --- Terminal tab management ---
const terminals = ref([{ index: 0, label: 'Main' }])
const activeIndex = ref(0)
const nextIndex = ref(1) // monotonically increasing counter
const pendingRouteTermIndex = ref(null)
const unavailableRouteTermIndex = ref(undefined)
let nextNavigationReplace = false
let syncingFromRoute = false
let backendIndicesReady = false

const isRouteTermUnavailable = computed(() =>
    unavailableRouteTermIndex.value !== undefined
    && (backendIndicesReady || unavailableRouteTermIndex.value === INVALID_ROUTE_TERM_INDEX)
)
const fallbackTermIndex = computed(() => findFallbackTermIndex(props.routeTermIndex ?? activeIndex.value))
const activeTabPanel = computed(() => isRouteTermUnavailable.value ? '__unavailable__' : `term-${activeIndex.value}`)
const activeApi = computed(() => terminalApis.get(activeIndex.value) || null)
const isActiveMain = computed(() => activeIndex.value === 0)
const unavailableRouteMessage = computed(() => (
    unavailableRouteTermIndex.value === INVALID_ROUTE_TERM_INDEX
        ? 'Requested terminal is not available.'
        : `Terminal \`${unavailableRouteTermIndex.value}\` is no longer available.`
))

// Flattened toolbar state from the active terminal's API.
// Note: reactive Map's .get() wraps results in reactive(), which auto-unwraps
// refs — so activeApi.value.isConnected returns the boolean directly, not a Ref.
const tb = reactive({
    get isConnected() { return activeApi.value?.isConnected ?? false },
    get canScrollUp() { return activeApi.value?.canScrollUp ?? false },
    get canScrollDown() { return activeApi.value?.canScrollDown ?? false },
    get paneAlternate() { return activeApi.value?.paneAlternate ?? false },
    get scrollingToEdge() { return activeApi.value?.scrollingToEdge ?? false },
    get hasSelection() { return activeApi.value?.hasSelection ?? false },
    get touchMode() { return activeApi.value?.touchMode ?? 'scroll' },
})

function createTerminal() {
    const index = nextIndex.value++
    terminals.value.push({ index, label: `Term ${index + 1}` })
    activeIndex.value = index
}

function findFallbackTermIndex(target = activeIndex.value) {
    const sorted = [...terminals.value].map(t => t.index).sort((a, b) => a - b)
    if (sorted.length === 0) return 0
    const lowerOrEqual = sorted.filter(index => index <= target)
    return lowerOrEqual.length ? lowerOrEqual[lowerOrEqual.length - 1] : sorted[0]
}

function syncActiveIndex(index) {
    syncingFromRoute = true
    activeIndex.value = index
    nextTick(() => {
        syncingFromRoute = false
    })
}

function replaceToTerm(index) {
    emit('navigate', { termIndex: index, replace: true })
    nextNavigationReplace = false
}

/** Remove a terminal tab (idempotent — no-op if already removed). */
function removeTerminalTab(index) {
    if (index === 0) return // main terminal tab is permanent
    const idx = terminals.value.findIndex(t => t.index === index)
    if (idx === -1) return
    terminals.value.splice(idx, 1)
    if (activeIndex.value === index) {
        const prevTerminal = terminals.value[Math.max(0, idx - 1)]
        nextNavigationReplace = false
        activeIndex.value = prevTerminal?.index ?? 0
    }
    // Eagerly remove from store so that syncTerminalsFromBackend doesn't
    // re-add this tab before the backend's terminal_killed broadcast arrives.
    terminalTabsStore.removeIndex(props.contextKey, index)
}

/** Kill a secondary terminal: send WS message to clean up tmux + remove tab. */
function killTerminal(index) {
    if (index === 0) return
    sendWsMessage({
        type: 'kill_terminal',
        terminal_context: props.contextKey,
        terminal_index: index,
    })
    removeTerminalTab(index)
}

/** Called when a TerminalInstance's WS disconnects (PTY died, Ctrl+D, network, etc.) */
function onTerminalDisconnected(index) {
    if (index === 0) return // main terminal: keep tab, show reconnect overlay
    removeTerminalTab(index)
}

function onTerminalTabShow(event) {
    const panelName = event.detail?.name
    if (panelName?.startsWith('term-')) {
        activeIndex.value = parseInt(panelName.slice(5), 10)
    }
}

// --- Terminal tab rename ---

/** Default label for a terminal index (used when no custom label is set). */
function defaultLabel(index) {
    return index === 0 ? 'Main' : `Term ${index + 1}`
}

/** Open the rename dialog for a terminal tab. */
function openRenameDialog(index) {
    const term = terminals.value.find(t => t.index === index)
    if (!term) return
    renameDialogRef.value?.open(index, term.label, defaultLabel(index))
}

/** Handle double-click on a tab: open rename dialog for that tab. */
function onTabDblClick(index) {
    openRenameDialog(index)
}

/** Handle save from the rename dialog. */
function handleRename(index, label) {
    const term = terminals.value.find(t => t.index === index)
    if (!term) return

    // Update local label immediately (optimistic)
    term.label = label || defaultLabel(index)

    // For tmux sessions, persist to backend
    if (usesTmux.value) {
        sendWsMessage({
            type: 'rename_terminal',
            terminal_context: props.contextKey,
            terminal_index: index,
            label,
        })
    }
}

// --- Snippet helpers ---

const SNIPPET_LABEL_MAX_LENGTH = 30

/**
 * Send a snippet to a terminal API, reconnecting first if the terminal
 * is disconnected. If already connected, sends immediately.
 */
function sendSnippetToApi(api, snippet) {
    if (api.isConnected) {
        api.handleSnippetPress(snippet)
        return
    }
    // Terminal is disconnected — reconnect and send once ready
    api.reconnect()
    const stopWatch = watch(
        () => api.isConnected,
        (connected) => {
            if (connected) {
                stopWatch()
                api.handleSnippetPress(snippet)
            }
        },
    )
    setTimeout(() => stopWatch(), 10000)
}

// --- Edit-before-send dialog ---

function handleSnippetEditSend(snippet) {
    snippetSendDialogRef.value?.open(snippet)
}

function handleSnippetSendDialogSend(editedSnippet, target) {
    handleSnippetSendTo(editedSnippet, target)
}

// --- Snippet dispatch (main button click) ---

function handleSnippetPress(snippet) {
    if (snippet.openInNewTab) {
        handleSnippetSendTo(snippet, 'new')
        return
    }
    const api = activeApi.value
    if (!api) return
    sendSnippetToApi(api, snippet)
}

// --- Send snippet to a specific terminal target ---

function handleSnippetSendTo(snippet, target) {
    if (target === 'new') {
        // Clean the snippet label (same sanitization as TerminalRenameDialog)
        const label = snippet.label.trim().slice(0, SNIPPET_LABEL_MAX_LENGTH)

        // Create a new terminal tab and name it after the snippet
        const newIndex = nextIndex.value
        createTerminal()
        const term = terminals.value.find(t => t.index === newIndex)
        if (term && label) {
            term.label = label
        }

        // Once the PTY chain is fully wired and the shell prompt has rendered:
        // send the snippet and persist the label to tmux. We wait on `isReady`
        // (not `isConnected`) because the latter fires on `websocket.accept`,
        // before tmux has attached the pane — input sent that early gets eaten.
        const stopWatch = watch(
            () => terminalApis.get(newIndex)?.isReady,
            (ready) => {
                if (ready) {
                    stopWatch()
                    terminalApis.get(newIndex)?.handleSnippetPress?.(snippet)
                    if (usesTmux.value && label) {
                        sendWsMessage({
                            type: 'rename_terminal',
                            terminal_context: props.contextKey,
                            terminal_index: newIndex,
                            label,
                        })
                    }
                }
            },
            { immediate: true },
        )
        // Safety: stop watching after 10 seconds to avoid leaks
        setTimeout(() => stopWatch(), 10000)
    } else {
        const targetIndex = Number(target)
        const api = terminalApis.get(targetIndex)
        if (api) {
            sendSnippetToApi(api, snippet)
            activeIndex.value = targetIndex
        }
    }
}

// --- External "launch a command in this terminal context" requests ---
//
// Other components (e.g. the Claude CLI not-authenticated toast) queue a
// command via the terminalCommand store. We pick it up here, always open a
// fresh new tab, make it the active (route-driven) tab so it actually starts,
// and send the command once the tab is ready.

const terminalCommandStore = useTerminalCommandStore()

watch(
    () => terminalCommandStore.pending[props.contextKey],
    (entry) => {
        if (!entry) return

        // Defer past this tick. The callers queue the command and then navigate
        // to the bare ``…/terminal`` URL (no termIndex), so the panel mounts with
        // a pending entry. The route-reconciliation watcher below runs right after
        // this one and, seeing no termIndex, forces activeIndex back to 0 — which
        // would clobber a tab created/activated synchronously here. Running on
        // nextTick lets that reset happen first; we then create the tab and drive
        // the route to it, so our navigation is the one that wins.
        nextTick(() => {
            const targetIndex = nextIndex.value
            createTerminal()

            // Make the new tab the active one *through the route* (the source of
            // truth for activeIndex). This is what flips TerminalInstance's
            // ``active`` prop and triggers start(); without it the WS never opens,
            // ``isReady`` never flips, and the command would only fire on a manual
            // tab click. ``replace`` so we don't stack an extra history entry on
            // top of the caller's push to the terminal view.
            emit('navigate', { termIndex: targetIndex, replace: true })

            // Wait on `isReady` (full PTY/tmux/shell chain alive) rather than
            // `isConnected` (WS accepted) so the command doesn't land before the
            // tmux pane is wired or the shell has rendered its prompt.
            const stopWatch = watch(
                () => terminalApis.get(targetIndex)?.isReady,
                (ready) => {
                    if (!ready) return
                    stopWatch()
                    terminalApis.get(targetIndex)?.handleSnippetPress?.({
                        snippet: entry.snippet,
                        appendEnter: entry.appendEnter,
                        placeholders: [],
                    })
                    terminalCommandStore.take(props.contextKey)
                },
                { immediate: true },
            )
            setTimeout(() => stopWatch(), 10000)
        })
    },
    { immediate: true },
)

watch(activeIndex, (newIndex) => {
    if (!props.active) return
    if (syncingFromRoute) return
    if (pendingRouteTermIndex.value != null) return
    if (newIndex === props.routeTermIndex) return
    emit('navigate', {
        termIndex: newIndex,
        replace: nextNavigationReplace,
    })
    nextNavigationReplace = false
})

function applyRouteTermIndex(target) {
    if (!props.active) return

    if (target === undefined) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = undefined
        if (props.routeTermIndex !== 0) {
            syncActiveIndex(0)
            replaceToTerm(0)
        } else {
            syncActiveIndex(0)
        }
        return
    }

    if (target === null) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = INVALID_ROUTE_TERM_INDEX
        syncActiveIndex(findFallbackTermIndex(0))
        return
    }

    if (terminals.value.some(term => term.index === target)) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = undefined
        syncActiveIndex(target)
        return
    }

    if (!backendIndicesReady) {
        pendingRouteTermIndex.value = target
        unavailableRouteTermIndex.value = undefined
        syncActiveIndex(findFallbackTermIndex(target))
        return
    }

    pendingRouteTermIndex.value = null
    unavailableRouteTermIndex.value = target
    syncActiveIndex(findFallbackTermIndex(target))
}

watch(
    () => [props.active, props.routeTermIndex, props.routeOwner],
    ([active, target]) => {
        if (!active || !props.routeOwner) return
        applyRouteTermIndex(target)
    },
    { immediate: true },
)

// Focus the active terminal when switching terminal sub-tabs
watch(activeIndex, () => {
    if (isRouteTermUnavailable.value) return
    nextTick(() => {
        activeApi.value?.focus?.()
    })
})

// Focus the active terminal when the terminal panel becomes active.
// When switching tabs via keyboard, the route (and thus props.active) changes before
// the wa-tab-group has actually shown the panel, so a single nextTick isn't enough.
// We retry with requestAnimationFrame (up to ~500ms) to cover both mouse and keyboard.
watch(() => props.active, (active) => {
    if (!active) return
    if (isRouteTermUnavailable.value) return
    let attempts = 0
    const maxAttempts = 30 // ~500ms at 60fps
    function tryFocus() {
        const api = activeApi.value
        if (api) {
            api.focus?.()
            // Check if focus actually landed on the terminal's textarea
            if (document.activeElement?.closest?.('.terminal-container')) return
        }
        if (++attempts < maxAttempts) {
            requestAnimationFrame(tryFocus)
        }
    }
    requestAnimationFrame(tryFocus)
})

// --- Toolbar action helpers (delegate to activeApi) ---

function handleScrollToEdge(direction) {
    const api = activeApi.value
    if (!api) return
    api.scrollingToEdge ? api.cancelScrollToEdge() : api.scrollToEdge(direction)
}

function handleTouchModeChange(event) {
    const api = activeApi.value
    if (!api) return
    api.touchMode = event.target.checked ? 'select' : 'scroll'
}

function handleTouchModeToggle() {
    const api = activeApi.value
    if (!api) return
    api.touchMode = api.touchMode === 'scroll' ? 'select' : 'scroll'
}

function handleCopy() {
    activeApi.value?.copySelection?.()
}

// --- Text selection comment ---

const terminalCommentRef = ref(null)
const terminalCommentText = ref('')
const terminalCommentPosition = ref(null)
const commentButtonRef = ref(null)

function handleComment() {
    const api = activeApi.value
    if (!api) return
    const text = api.getSelectionText?.()
    if (!text) return

    // Position below the comment button
    const btn = commentButtonRef.value
    if (btn) {
        const rect = btn.getBoundingClientRect()
        terminalCommentPosition.value = {
            top: rect.bottom,
            left: rect.left + rect.width / 2,
            above: false,
        }
    }
    terminalCommentText.value = text
}

function closeTerminalComment() {
    terminalCommentPosition.value = null
    terminalCommentText.value = ''
}

function handlePaste() {
    activeApi.value?.handleExtraKeyPaste?.()
}

function handleDisconnect() {
    // Send Ctrl+D (EOF) to the active terminal. The shell exits naturally,
    // the backend sends pty_exited, and the tab is removed for secondary terminals.
    // For the main terminal, the tab stays with the reconnect overlay.
    activeApi.value?.disconnect?.()
}

// --- Discovery and cross-device sync ---

let discoveryDone = false

function requestTerminalDiscovery() {
    if (!props.active) return
    if (discoveryDone) return
    if (!dataStore.wsConnected) return
    const sent = sendWsMessage({
        type: 'list_terminals',
        terminal_context: props.contextKey,
    })
    if (sent) {
        discoveryDone = true
    }
}

// When the panel first becomes active and the WebSocket is ready, request the
// terminal list from the backend. On direct page loads, the panel can mount
// before the socket is open, so we retry when wsConnected flips to true.
watch(
    [() => props.active, () => dataStore.wsConnected],
    () => {
        requestTerminalDiscovery()
    },
    { immediate: true },
)

// Watch the terminalTabsStore for backend terminal updates
watch(
    () => terminalTabsStore.indices[props.contextKey],
    (backendIndices, oldIndices) => {
        if (!backendIndices) return
        backendIndicesReady = true
        syncTerminalsFromBackend(backendIndices, oldIndices)
        if (pendingRouteTermIndex.value != null) {
            applyRouteTermIndex(pendingRouteTermIndex.value)
        }
    },
    { immediate: true },
)

function syncTerminalsFromBackend(backendIndices, oldIndices) {
    const localIndices = new Set(terminals.value.map(t => t.index))

    // Add tabs for backend terminals not present locally
    for (const index of backendIndices) {
        if (!localIndices.has(index)) {
            // Use label from store if available, otherwise default
            const storeLabel = terminalTabsStore.getLabel(props.contextKey, index)
            const label = storeLabel || defaultLabel(index)
            terminals.value.push({ index, label })
        }
    }

    // Remove tabs for terminals killed from another device
    // (only if we have old indices to compare — skip on first load)
    if (oldIndices) {
        const removedIndices = oldIndices.filter(i => !backendIndices.includes(i))
        for (const index of removedIndices) {
            if (index === 0) continue // main terminal never removed
            const idx = terminals.value.findIndex(t => t.index === index)
            if (idx !== -1) {
                terminals.value.splice(idx, 1)
                if (activeIndex.value === index) {
                    const prevTerminal = terminals.value[Math.max(0, idx - 1)]
                    nextNavigationReplace = false
                    activeIndex.value = prevTerminal?.index ?? 0
                }
            }
        }
    }

    // Sort terminals by index for consistent ordering
    terminals.value.sort((a, b) => a.index - b.index)

    // Update nextIndex to avoid collisions
    const maxIndex = Math.max(0, ...backendIndices, ...terminals.value.map(t => t.index))
    if (maxIndex >= nextIndex.value) {
        nextIndex.value = maxIndex + 1
    }
}

// Watch store labels for cross-device sync (another client renamed a terminal)
watch(
    () => terminalTabsStore.labels[props.contextKey],
    (storeLabels) => {
        if (!storeLabels) return
        for (const term of terminals.value) {
            const storeLabel = storeLabels[term.index]
            if (storeLabel) {
                term.label = storeLabel
            } else if (!storeLabel && terminalTabsStore.indices[props.contextKey]?.includes(term.index)) {
                // Label was cleared in the store for a known backend terminal → reset to default
                term.label = defaultLabel(term.index)
            }
        }
    },
    { deep: true },
)

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: terminal tab navigation (Alt+Ctrl+Shift+{1-9, ←/→, ↑})
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Tab visit history for Alt+Ctrl+Shift+↑ (last-visited, Alt+Tab-like behavior).
const terminalTabHistory = []
const MAX_TERMINAL_TAB_HISTORY = 50

function pushTerminalTabHistory(termIndex) {
    if (terminalTabHistory.length > 0 && terminalTabHistory[terminalTabHistory.length - 1] === termIndex) return
    terminalTabHistory.push(termIndex)
    if (terminalTabHistory.length > MAX_TERMINAL_TAB_HISTORY) terminalTabHistory.shift()
}

// Track terminal tab transitions for history
watch(activeIndex, (newIndex, oldIndex) => {
    if (!props.active) return
    if (oldIndex !== undefined && oldIndex !== newIndex) pushTerminalTabHistory(oldIndex)
})

function handleTerminalTabShortcut(event) {
    if (!props.active) return

    const { type, index } = event.detail

    if (type === 'direct') {
        // Direct access: number N → the Nth terminal tab (1-based positional)
        const term = terminals.value[index - 1]
        if (term) activeIndex.value = term.index
    } else if (type === 'prev' || type === 'next') {
        const currentIdx = terminals.value.findIndex(t => t.index === activeIndex.value)
        if (currentIdx === -1) return
        const newIdx = type === 'next'
            ? (currentIdx + 1) % terminals.value.length
            : (currentIdx - 1 + terminals.value.length) % terminals.value.length
        activeIndex.value = terminals.value[newIdx].index
    } else if (type === 'last-visited') {
        const validIndices = new Set(terminals.value.map(t => t.index))
        for (let i = terminalTabHistory.length - 1; i >= 0; i--) {
            const idx = terminalTabHistory[i]
            if (idx !== activeIndex.value && validIndices.has(idx)) {
                activeIndex.value = idx
                return
            }
        }
    }
}

onMounted(() => {
    window.addEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
})

defineExpose({ activeIndex })
</script>

<template>
    <div class="terminal-panel">
        <!-- Merged toolbar: terminal tabs (left) + action buttons (right) -->
        <div class="terminal-actions-bar">
            <!-- Left: wa-tab-group used only for its scrollable nav -->
            <wa-tab-group
                :active="activeTabPanel"
                class="terminal-tab-nav"
                @wa-tab-show="onTerminalTabShow"
            >
                <wa-tab
                    v-if="isRouteTermUnavailable"
                    slot="nav"
                    panel="__unavailable__"
                    class="terminal-unavailable-tab"
                    aria-hidden="true"
                    tabindex="-1"
                ></wa-tab>

                <wa-tab
                    v-for="term in terminals"
                    :key="term.index"
                    slot="nav"
                    :panel="`term-${term.index}`"
                    @dblclick="onTabDblClick(term.index)"
                >
                    {{ term.label }}
                </wa-tab>

                <wa-button
                    slot="nav"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    class="add-terminal-button reduced-height"
                    @click="createTerminal"
                >
                    <wa-icon name="plus"></wa-icon>
                </wa-button>
            </wa-tab-group>

            <!-- Right: rename button (always visible) + terminal-specific actions (when connected) -->
            <div v-if="!isRouteTermUnavailable" class="terminal-actions">
                <template v-if="tb.isConnected">
                    <!-- Scroll to edge buttons -->
                    <wa-button
                        v-if="tb.canScrollUp || tb.paneAlternate"
                        id="terminal-scroll-top-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="scroll-edge-button reduced-height"
                        :loading="tb.scrollingToEdge"
                        @click="handleScrollToEdge('top')"
                    >
                        <wa-icon name="angles-up"></wa-icon>
                    </wa-button>
                    <AppTooltip
                        v-if="tb.canScrollUp || tb.paneAlternate"
                        for="terminal-scroll-top-button"
                    >Scroll to top</AppTooltip>

                    <wa-button
                        v-if="tb.canScrollDown || tb.paneAlternate"
                        id="terminal-scroll-bottom-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="scroll-edge-button reduced-height"
                        :loading="tb.scrollingToEdge"
                        @click="handleScrollToEdge('bottom')"
                    >
                        <wa-icon name="angles-down"></wa-icon>
                    </wa-button>
                    <AppTooltip
                        v-if="tb.canScrollDown || tb.paneAlternate"
                        for="terminal-scroll-bottom-button"
                    >Scroll to bottom</AppTooltip>

                    <!-- Mobile-only: scroll/select mode toggle -->
                    <div v-if="settingsStore.isTouchDevice" class="touch-mode-group">
                        <span
                            class="touch-mode-label"
                            @click="handleTouchModeToggle"
                        >Scroll</span>
                        <wa-switch
                            size="small"
                            class="touch-mode-switch"
                            :checked="tb.touchMode === 'select'"
                            @change="handleTouchModeChange"
                        >Select</wa-switch>
                    </div>

                    <!-- Comment button (only when a message input is available) -->
                    <wa-button
                        v-if="tb.hasSelection && insertTextAtCursor"
                        ref="commentButtonRef"
                        id="terminal-comment-button"
                        variant="brand"
                        appearance="filled-outlined"
                        size="small"
                        class="comment-button reduced-height"
                        @click="handleComment"
                    >
                        <wa-icon name="comment" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="tb.hasSelection && insertTextAtCursor" for="terminal-comment-button">Comment on selection</AppTooltip>

                    <!-- Copy button (all devices) -->
                    <wa-button
                        v-if="tb.hasSelection"
                        id="terminal-copy-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="copy-button reduced-height"
                        @click="handleCopy"
                    >
                        <wa-icon name="copy" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="tb.hasSelection" for="terminal-copy-button">Copy selection</AppTooltip>

                    <!-- Paste button -->
                    <wa-button
                        id="terminal-paste-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="paste-button reduced-height"
                        @click="handlePaste"
                    >
                        <wa-icon name="paste" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-paste-button">Paste from clipboard</AppTooltip>
                </template>

                <wa-divider v-if="tb.isConnected" orientation="vertical"></wa-divider>

                <!-- Rename button — always available -->
                <wa-button
                    id="terminal-rename-button"
                    variant="neutral"
                    appearance="filled"
                    size="small"
                    class="rename-button reduced-height"
                    @click="openRenameDialog(activeIndex)"
                >
                    <wa-icon name="pen-to-square" variant="regular"></wa-icon>
                </wa-button>
                <AppTooltip for="terminal-rename-button">Rename tab</AppTooltip>

                <template v-if="tb.isConnected">

                    <!-- Disconnect / Kill button -->
                    <wa-button
                        id="terminal-disconnect-button"
                        variant="danger"
                        appearance="filled"
                        size="small"
                        class="disconnect-button reduced-height"
                        @click="handleDisconnect"
                    >
                        <wa-icon name="ban" :label="isActiveMain ? 'Disconnect' : 'Kill terminal'"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-disconnect-button">{{ isActiveMain ? 'Disconnect' : 'Kill terminal' }}</AppTooltip>
                </template>
            </div>
        </div>

        <div v-if="isRouteTermUnavailable" class="terminal-unavailable-state">
            <wa-callout variant="warning" appearance="filled-outlined" class="terminal-unavailable-callout">
                {{ unavailableRouteMessage }}
            </wa-callout>
        </div>

        <!-- Terminal panels: all overlay each other, only the active one is visible.
             Uses visibility:hidden (not display:none) so hidden terminals keep their
             dimensions — prevents xterm.js resize flash on tab switch. -->
        <div v-if="!isRouteTermUnavailable" class="terminal-panels-container">
            <div
                v-for="term in terminals"
                :key="term.index"
                :class="['terminal-panel-wrapper', { active: activeIndex === term.index }]"
            >
                <TerminalInstance
                    :context-key="props.contextKey"
                    :session-id="props.sessionId"
                    :project-id="resolvedProjectId"
                    :cwd="props.cwd"
                    :terminal-index="term.index"
                    :active="active && activeIndex === term.index"
                    @disconnected="onTerminalDisconnected(term.index)"
                />
            </div>
        </div>

        <TerminalExtraKeysBar
            v-if="!isRouteTermUnavailable"
            :active-modifiers="activeApi?.activeModifiers ?? { ctrl: false, alt: false, shift: false }"
            :locked-modifiers="activeApi?.lockedModifiers ?? { ctrl: false, alt: false, shift: false }"
            :is-touch-device="settingsStore.isTouchDevice"
            :combos="terminalConfigStore.combos"
            :snippets="snippetsForProject"
            :terminals="terminals"
            :active-terminal-index="activeIndex"
            @key-input="(...args) => activeApi?.handleExtraKeyInput?.(...args)"
            @modifier-toggle="(...args) => activeApi?.handleExtraKeyModifierToggle?.(...args)"
            @paste="() => activeApi?.handleExtraKeyPaste?.()"
            @combo-press="(...args) => activeApi?.handleComboPress?.(...args)"
            @snippet-press="handleSnippetPress"
            @snippet-send-to="handleSnippetSendTo"
            @snippet-edit-send="handleSnippetEditSend"
            @snippet-disabled-press="(snippet) => toast.warning(snippet._disabledReason)"
            @manage-combos="manageCombosDialogRef?.open()"
            @manage-snippets="manageSnippetsDialogRef?.open()"
        />

        <TerminalCombosDialog ref="manageCombosDialogRef" />
        <TerminalSnippetsDialog
            ref="manageSnippetsDialogRef"
            :current-project-id="snippetListProjectId"
        />
        <TerminalSnippetSendDialog
            ref="snippetSendDialogRef"
            :terminals="terminals"
            :active-terminal-index="activeIndex"
            :placeholder-context="placeholderContext"
            @send="handleSnippetSendDialogSend"
        />
        <TerminalRenameDialog
            ref="renameDialogRef"
            @save="handleRename"
        />

        <!-- Ephemeral text selection comment widget (teleported to body to avoid overflow clipping) -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="terminalCommentPosition"
                ref="terminalCommentRef"
                :selected-text="terminalCommentText"
                :position="terminalCommentPosition"
                auto-expand
                source-label="from terminal"
                @close="closeTerminalComment"
            />
        </Teleport>
    </div>
</template>

<style scoped>
.terminal-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
}

.terminal-unavailable-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-m);
}

.terminal-unavailable-callout {
    flex: 0 0 auto;
    width: auto;
    max-width: min(32rem, 100%);
}

/* ── Merged toolbar ─────────────────────────────────────── */

.terminal-actions-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: start;
    column-gap: var(--wa-space-m);
    border-bottom: var(--divider-size) solid var(--wa-color-surface-border);
    flex-shrink: 0;
    min-height: 2rem;
}

/* wa-tab-group used only for its scrollable nav — hide its body */
.terminal-tab-nav {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    font-size: var(--wa-font-size-s);
    --track-width: var(--divider-size);
    margin-bottom: calc(-1.5 * var(--divider-size));
    padding-top: 2.5px;
}
.terminal-tab-nav::part(tabs) {
    border-bottom-color: transparent;
}
.terminal-tab-nav::part(base) {
    overflow: hidden;
}
.terminal-tab-nav::part(body) {
    display: none;
}
.terminal-tab-nav::part(nav) {
    border-bottom: none;
    padding-bottom: 0;
}
.terminal-tab-nav::part(tabs) {
    align-items: center;
}
.terminal-tab-nav wa-tab::part(base) {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    gap: var(--wa-space-2xs);
}
.terminal-unavailable-tab {
    display: none;
}
.add-terminal-button {
    margin-left: var(--wa-space-2xs);
    align-self: center;
}

.terminal-actions {
    margin-inline-start: auto;
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-shrink: 0;
    wa-divider {
        --spacing: 0px;
    }
    padding-right: var(--wa-space-xs);
}

.scroll-edge-button {
    opacity: 0.5;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.scroll-edge-button:hover {
    opacity: 1;
}

.disconnect-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.disconnect-button:hover:not([disabled]) {
    opacity: 1;
}

.touch-mode-group {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.touch-mode-label {
    cursor: pointer;
    font-size: var(--wa-font-size-s);
    user-select: none;
}

.rename-button,
.copy-button,
.paste-button {
    flex-shrink: 0;
}

/* ── Terminal panels ─────────────────────────────────────── */

.terminal-panels-container {
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
}

.terminal-panel-wrapper {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    visibility: hidden;
}

.terminal-panel-wrapper.active {
    visibility: visible;
}
</style>
