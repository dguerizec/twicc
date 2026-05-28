// frontend/src/stores/data.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { toRaw } from 'vue'
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility'
import { computeVisualItems, visualItemEqual } from '../utils/visualItems'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants'
import { getProviderHelpers } from '../providers'
import { getSessionCutoffMs } from '../utils/sessions'
import { useSettingsStore } from './settings'
import {
    saveDraftMessage,
    getDraftMessage,
    deleteDraftMessage,
    getAllDraftMessages,
    saveDraftSession,
    getDraftSession,
    deleteDraftSession as deleteDraftSessionFromDb,
    getAllDraftSessions,
    saveDraftMedia,
    deleteDraftMedia,
    getDraftMediasBySession,
    deleteAllDraftMediasForSession,
    getAllDraftMedias
} from '../utils/draftStorage'
import {
    processFile,
    mediasToSdkFormat,
    getDraftMediaBytes,
    MAX_FILES_PER_DRAFT,
    MAX_TOTAL_BYTES_PER_DRAFT,
} from '../utils/fileUtils'
import { generateUUID } from '../utils/crypto'
import { debounce } from '../utils/debounce'
import { apiFetch } from '../utils/api'
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'
import { getParsedContent, setParsedContent, clearParsedContent, hasContent } from '../utils/parsedContent'
import { initBuffer, feedDelta, flushBuffer, destroySessionBuffers, destroyAllBuffers } from '../utils/streamingBuffer'

// Map of debounced save functions per session (to avoid mixing debounces)
const debouncedSaves = new Map()

// How long a ``text`` streaming block can stay quiet before we flip its
// ``stopped`` flag and let the WorkingAssistantMessage placeholder reappear.
// Used by streamBlockDelta below. Codex's ``item/completed`` event can lag
// the last actual ``agentMessage/delta`` by several seconds (15+ observed),
// during which the SDK has nothing more to say but the agent is technically
// still working — without this nudge the UI looks frozen with no indicator.
const STREAM_BLOCK_INACTIVITY_MS = 500

// Cancel any pending inactivity timer attached to a streaming block. Safe
// to call when no timer is set. Called from streamBlockStop / start /
// retire / process-state-dead paths so we never leak a setTimeout.
function clearBlockInactivityTimer(block) {
    if (block?._inactivityTimer) {
        clearTimeout(block._inactivityTimer)
        block._inactivityTimer = null
    }
}

function userMessageMatchesOptimistic(providerHelpers, optimistic, item) {
    if (!providerHelpers || !optimistic || item?.kind !== 'user_message') return false

    const optimisticText = providerHelpers.extractUserMessageText(getParsedContent(optimistic))
    if (!optimisticText) return false

    const parsed = getParsedContent(item)
    const createdAtMs = optimistic._optimisticCreatedAtMs
    const itemTimestampMs = typeof parsed?.timestamp === 'string'
        ? Date.parse(parsed.timestamp)
        : Number.NaN
    if (createdAtMs && Number.isFinite(itemTimestampMs) && itemTimestampMs < createdAtMs - 1000) {
        return false
    }

    const itemText = providerHelpers.extractUserMessageText(parsed)
    return itemText === optimisticText
}

// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'

// Aggregate per-provider startup-progress entries for a single phase into the
// flat ``{ current, total, completed }`` shape consumed by the UI. Returns
// null when no provider has reported yet so callers can stay falsy-aware.
function aggregatePhase(byProvider) {
    if (!byProvider) return null
    const entries = Object.values(byProvider)
    if (entries.length === 0) return null
    let current = 0
    let total = 0
    let completed = true
    for (const entry of entries) {
        current += entry.current ?? 0
        total += entry.total ?? 0
        if (!entry.completed) completed = false
    }
    return { current, total, completed }
}

// Cheap "still booting" probe used by hot getters (unread counts, sessions
// list) to skip expensive work while any provider is still mid-phase.
function hasActiveStartupPhase(startupProgress) {
    for (const byProvider of Object.values(startupProgress)) {
        if (!byProvider) continue
        for (const entry of Object.values(byProvider)) {
            if (entry && !entry.completed) return true
        }
    }
    return false
}

/**
 * Sort sessions by display priority:
 * 1. Pinned sessions first (top-level split — any non-null pin mode counts).
 * 2. Within each pin group: sessions with active process first (by started_at
 *    descending for stable ordering).
 * 3. Remaining sessions within each pin group: by mtime descending.
 *
 * @param {Object} processStates - Map of sessionId -> processState
 * @returns {function} Comparator function for Array.sort()
 */
export function sessionSortComparator(processStates) {
    return (a, b) => {
        // 1. Pinned sessions first (regardless of mode).
        //    `pinned` is a string ('project'/'workspace'/'all') or null — any truthy
        //    value means pinned.
        const aPinned = !!a.pinned
        const bPinned = !!b.pinned
        if (aPinned !== bPinned) return aPinned ? -1 : 1

        // 2. Within the same pin group: sessions with active process first.
        const aProcess = processStates[a.id]
        const bProcess = processStates[b.id]
        const aHasProcess = aProcess != null
        const bHasProcess = bProcess != null
        if (aHasProcess !== bHasProcess) return aHasProcess ? -1 : 1

        // 3. Among active sessions: sort by started_at descending (most recently started first).
        //    This gives a stable order since started_at never changes during process lifetime,
        //    avoiding rapid swapping when multiple sessions update frequently.
        if (aHasProcess && bHasProcess) {
            return (bProcess.started_at || 0) - (aProcess.started_at || 0)
        }

        // 4. Non-active sessions: sort by mtime descending.
        return b.mtime - a.mtime
    }
}

/**
 * Compute display metadata for a streaming synthetic item.
 *
 * Decides display_level / group_head / group_tail based on the block's type
 * and surrounding context, so the synthetic item participates in the existing
 * grouping/visibility logic in visualItems.js.
 *
 * Rules:
 *   - text block:
 *       display_level = ALWAYS, no group.
 *   - thinking block:
 *       display_level = COLLAPSIBLE.
 *       group_head:
 *         - if the last real item is in an open group (COLLAPSIBLE with
 *           group_head set, OR ALWAYS with group_tail set), join it.
 *         - otherwise, become own group head (group_head = self.line_num).
 *       group_tail = null (a streaming thinking is never a suffix anchor).
 *
 * Note on conversation mode: streaming items are always hidden in conversation
 * mode unless the current block is in detailed mode. That filtering happens
 * in visualItems.js, not here.
 *
 * @param {Object} block - a streaming block: { blockIndex, blockType, ... }
 * @param {Object|null} lastRealItem - last DISPLAYABLE item (display_level
 *   ALWAYS or COLLAPSIBLE) in sessionItems before streaming was injected.
 *   Caller must scan past DEBUG_ONLY items and items with null display_level
 *   so the anchor reflects the last item the user actually sees.
 * @param {number} streamingLineNum - the synthetic line_num for this block
 *   (= SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex).
 * @returns {{display_level: number, group_head: number|null, group_tail: number|null}}
 */
function getStreamingItemMetadata(block, lastRealItem, streamingLineNum) {
    if (block.blockType === 'text') {
        return {
            display_level: DISPLAY_LEVEL.ALWAYS,
            group_head: null,
            group_tail: null,
        }
    }

    // Thinking block.
    let groupHead = streamingLineNum  // default: own fake group
    if (lastRealItem) {
        if (
            lastRealItem.display_level === DISPLAY_LEVEL.COLLAPSIBLE &&
            lastRealItem.group_head != null
        ) {
            // Join the existing COLLAPSIBLE group.
            groupHead = lastRealItem.group_head
        } else if (
            lastRealItem.display_level === DISPLAY_LEVEL.ALWAYS &&
            lastRealItem.group_tail != null
        ) {
            // Continue the ALWAYS-suffix group started by lastRealItem.
            groupHead = lastRealItem.line_num
        }
    }
    return {
        display_level: DISPLAY_LEVEL.COLLAPSIBLE,
        group_head: groupHead,
        group_tail: null,
    }
}

/**
 * Whether the tool card the working-message would point to is actually visible
 * on screen, given the current display mode and expansion state.
 *
 * The working-message component drops the parenthesised target (e.g. the file
 * path) when there's a single active tool and its card sits right above. That
 * shortcut assumes the card is visible; in simplified mode the tool may live
 * inside a collapsed group, and in conversation mode the whole non-user block
 * is hidden unless "show details" is open.
 *
 * @param {Array} items - real session items (no synthetic).
 * @param {string|null} lastStartedToolId - id of the most recently started tool.
 * @param {string} mode - effective DISPLAY_MODE.
 * @param {Array<number>} expandedGroups - line_nums of expanded group heads.
 * @param {boolean} isCurrentBlockDetailed - conversation mode: whether the
 *   current user block has its detail toggle open.
 * @returns {boolean}
 */
function computeLastToolVisible(items, lastStartedToolId, mode, expandedGroups, isCurrentBlockDetailed) {
    if (mode === DISPLAY_MODE.NORMAL || mode === DISPLAY_MODE.DEBUG) return true
    if (!lastStartedToolId) return false
    for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (it.kind !== 'assistant_message') continue
        const parsed = getParsedContent(it)
        const blocks = parsed?.message?.content
        if (!Array.isArray(blocks)) continue
        const hasTool = blocks.some(b => b?.type === 'tool_use' && b?.id === lastStartedToolId)
        if (!hasTool) continue
        if (it.display_level === DISPLAY_LEVEL.DEBUG_ONLY) return false
        if (mode === DISPLAY_MODE.CONVERSATION) return isCurrentBlockDetailed
        if (mode === DISPLAY_MODE.SIMPLIFIED) {
            if (it.display_level === DISPLAY_LEVEL.ALWAYS) return true
            const head = it.group_head ?? it.line_num
            return expandedGroups.includes(head)
        }
        return true
    }
    return false
}

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, stale } }
        sessions: {},       // { id: { id, project_id, provider, last_line, mtime, stale } }
        // Session items indexed by session ID.
        // { sessionId: [{ line_num, content, display_level, ... }] } - line_num is 1-based
        //
        // ⚠️  IMPORTANT: Never access item.content directly for parsing.
        // Use getParsedContent(item) from utils/parsedContent.js instead.
        // Use hasContent(item) to check if content is available.
        sessionItems: {},

        // Process state for active Claude processes
        // { sessionId: { state: 'starting'|'assistant_turn'|'user_turn'|'dead', error?: string } }
        processStates: {},

        // Lifecycle state of each provider's orchestrator (mirrors backend
        // `twicc.providers.state.ProviderState`). Updated from the bootstrap
        // payload and live `provider_state_changed` WS messages. Used by
        // `isProviderAvailable` (provider must be both intent-enabled AND
        // in 'running' state) to gate runtime UI (callouts, pickers, ...).
        // Default fallback when a provider is missing from the map: 'stopped'.
        // { 'claude_code': 'running', 'codex': 'stopped' }
        providerStates: {},

        // Weekly activity data (from /api/home/ endpoint)
        // { _global: [...], projectId: [...] } — each value is Array of { date, user_message_count }
        weeklyActivity: {},

        // WebSocket connection state (updated by useWebSocket composable)
        wsConnected: false,

        // Startup progress (from WebSocket startup_progress messages).
        // Indexed by phase, then by provider key — so the per-phase total
        // displayed in the UI is the sum of every provider that emitted
        // progress for that phase. Provider-agnostic phases (e.g.
        // ``search_index``) bucket under ``__global__``.
        // Shape: { [phase]: { [provider | '__global__']: { current, total, completed } } }
        startupProgress: {},

        // Server info (from WebSocket messages)
        currentVersion: null,           // string, from server_version message
        pendingChangelogVersion: null,  // string, version to show in changelog dialog after app is ready
        previousChangelogVersion: null, // string, previousLastChangelogVersionSeen from backend
        latestVersion: null,            // { version, releaseUrl } or null, from update_available message

        // Local UI state (separate from server data to avoid being overwritten)
        localState: {
            projectsList: {
                loading: false,
                loadingError: false
            },
            projects: {},   // { projectId: { sessionsFetched, sessionsLoading, sessionsLoadingError, hasMoreSessions, oldestSessionMtime } }
            sessions: {},   // { sessionId: { itemsFetched, itemsLoading, itemsLoadingError } }

            // Expanded groups - per session (session-level groups)
            // { sessionId: [groupHeadLineNum, ...] }
            // Using array instead of Set for Vue reactivity
            sessionExpandedGroups: {},

            // Expanded internal groups - per session, per item (content-level groups within ALWAYS items)
            // { sessionId: { lineNum: [startIndex, ...] } }
            // Two-level structure allows easy invalidation of entire session
            sessionInternalExpandedGroups: {},

            // Blocks expanded to detailed mode in conversation view.
            // { sessionId: [userMessageLineNum, ...] }
            // Each entry is the line_num of the last user_message before a non-user block.
            // When present, all non-user items following that user_message (up to the next
            // user_message) are rendered in detailed/normal mode instead of conversation mode.
            // Using array instead of Set for Vue reactivity (same pattern as sessionExpandedGroups).
            // Ephemeral: not persisted, lost on page refresh.
            sessionDetailedBlocks: {},

            // Visual items - computed from sessionItems, display mode, and expanded groups
            // { sessionId: [{ lineNum, isGroupHead?, isExpanded? }, ...] }
            sessionVisualItems: {},

            // Visual item reference cache - used to stabilize object references
            // across recomputes so Vue skips re-renders for unchanged items.
            // { sessionId: Map<lineNum, visualItem> }
            // Not reactive (plain object + Maps) — only used internally by
            // recomputeVisualItems, never read by Vue templates.
            visualItemCache: {},

            // Open tabs per session - for tab restoration when returning to a session
            // { sessionId: { tabs: ['main', 'agent-xxx', ...], activeTab: 'agent-xxx' } }
            // Note: 'main' is always implicitly open, but included for consistency
            sessionOpenTabs: {},

            // Agent links cache - maps tool_id to agent_id for Task tool_use items
            // { sessionId: { toolId: agentId } }
            // Only caches found agents (not-found triggers polling, not caching)
            agentLinks: {},

            // Tool states - maps tool_use_id to { resultCount, completedAt, error, extra, toolResultLineNums }
            // { sessionId: { toolUseId: { resultCount, completedAt, error, extra, toolResultLineNums } } }
            // Populated by fetchToolStates on session load and WS tool_state
            toolStates: {},

            // Live items - tracks which session items arrived via WebSocket (real-time).
            // { sessionId: Set<lineNum> }
            // Used by auto-open live edit diffs feature: only items received in real-time
            // should auto-open, not historical items loaded from the API.
            liveItems: {},

            // Open wa-details state - persists open/close across virtual scroller mount/unmount.
            // { sessionId: { key: true, ... } }
            // Keys: toolId for tool_use details, `result:${toolId}` for tool result details.
            // Only open items are stored (sparse map). Ephemeral: not persisted, lost on refresh.
            openDetails: {},

            // Project display names cache - computed from name, directory, or id
            // { projectId: displayName }
            // Updated when project data changes
            projectDisplayNames: {},

            // Draft messages - unsent messages/titles per session
            // { sessionId: { message?: string, title?: string } }
            // Persisted to IndexedDB with debounce
            draftMessages: {},

            // Title suggestions by session ID
            // Format: { sessionId: { suggestion: string, sourcePrompt?: string } }
            titleSuggestions: {},

            // Map { draftId: canonicalId } populated by ``bindDraftSession`` so
            // any backend message that still carries the draft id (e.g. a
            // ``title_suggested`` whose ``suggest_title`` was sent before the
            // bind) can be redirected to the canonical key. Lives for the
            // session's lifetime; entries don't go stale because the canonical
            // id is what every consumer now uses.
            draftAliases: {},

            // Sessions waiting on an auto-applied title — populated when the
            // user sends the first message of a draft and ``titleAutoApply`` is
            // enabled. The App-level watcher (in ``App.vue``) reacts to entries
            // here, waits for the matching ``titleSuggestions`` entry, applies
            // it to the session and persists it via :meth:`renameSession` once
            // the session has stopped being a draft. Each entry stores the
            // ``projectId`` because that's what ``renameSession`` needs and
            // the watcher otherwise has no way to recover it.
            // Format: { sessionId: { projectId: string } }
            pendingTitleAutoApply: {},

            // Draft attachments - media files pending send per session
            // { sessionId: Map<mediaId, DraftMedia> }
            // Stored separately from draftMessages to avoid rewriting large blobs on each keystroke
            attachments: {},

            // Number of files currently being processed (encoded/resized) per session.
            // { sessionId: number }
            // Used to block the send button until all files are ready.
            processingAttachments: {},

            // MRU (Most Recently Used) navigation tracking
            // Ordered array of { path, sessionId } entries, most recent first
            // path: the full route path (e.g. /project/abc/session/xyz/files)
            // sessionId: the session ID from the route, or null if no session selected
            // Used to navigate back when archiving the current session
            mruPaths: [],

            // Optimistic messages - user messages displayed immediately after send,
            // before the backend confirms with a real user_message item.
            // { sessionId: { syntheticKind, content, kind } }
            // Cleared when the real user_message arrives in addSessionItems.
            optimisticMessages: {},

            // Streaming blocks - live text/thinking deltas from the SDK stream.
            // { sessionId: { messageId, blocks: [{ blockIndex, blockType, text, stopped, uuid }] } }
            // Each block is rendered as a synthetic visual item until the real
            // SessionItem (matched by message_id + uuid) arrives from the watcher.
            // `stopped` is set to true when content_block_stop fires (text is final
            // but uuid not yet known). While any block has stopped=false, the
            // WorkingAssistantMessage is hidden (streaming is actively showing content).
            streamingBlocks: {},

            // Pending draft → canonical session bindings, armed when a
            // `session_bound` WS message arrives before the canonical
            // session is in the store (i.e. before the watcher's
            // `session_updated`). { draftSessionId: sessionId }
            // Drained by tryFinalizePendingBinding() the moment the
            // canonical session lands in the store.
            pendingDraftBindings: {},
        }
    }),

    getters: {
        // Data getters (sorted by mtime descending - most recent first)
        getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
        getProject: (state) => (id) => state.projects[id],
        getProjectSessions: (state) => (projectId) => {
            const projectState = state.localState.projects[projectId]
            // Only apply the mtime lower-bound when there are more pages to load.
            // When all pages have been fetched (hasMoreSessions=false), every
            // session in the store should be visible — including ones added via
            // WS during background compute whose mtime may be older than the bound.
            const oldestMtime = projectState?.hasMoreSessions
                ? projectState.oldestSessionMtime
                : null
            // During startup, skip per-property reactive tracking on sessions.
            // Object.keys() tracks ITERATE_KEY (add/remove triggers re-eval),
            // then toRaw() avoids the ~23K track() calls per eval from filter/sort
            // property accesses. Normal tracking resumes after startup.
            const isStartup = hasActiveStartupPhase(state.startupProgress)
            let sessions, pStates
            if (isStartup) {
                Object.keys(state.sessions)
                const raw = toRaw(state.sessions)
                sessions = Object.values(raw)
                pStates = toRaw(state.processStates)
            } else {
                sessions = Object.values(state.sessions)
                pStates = state.processStates
            }
            return sessions
                .filter(s => s.project_id === projectId && !s.parent_session_id)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(pStates))
        },
        getAllSessions: (state) => {
            const allState = state.localState.projects[ALL_PROJECTS_ID]
            const oldestMtime = allState?.hasMoreSessions
                ? allState.oldestSessionMtime
                : null
            // During startup, skip per-property reactive tracking on sessions.
            // Object.keys() tracks ITERATE_KEY (add/remove triggers re-eval),
            // then toRaw() avoids the ~23K track() calls per eval from filter/sort
            // property accesses. Normal tracking resumes after startup.
            const isStartup = hasActiveStartupPhase(state.startupProgress)
            let sessions, pStates
            if (isStartup) {
                Object.keys(state.sessions)
                const raw = toRaw(state.sessions)
                sessions = Object.values(raw)
                pStates = toRaw(state.processStates)
            } else {
                sessions = Object.values(state.sessions)
                pStates = state.processStates
            }
            return sessions
                .filter(s => !s.parent_session_id)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(pStates))
        },
        getSession: (state) => (id) => state.sessions[id],
        getSessionProvider: (state) => (sessionId) => state.sessions[sessionId]?.provider ?? null,
        getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || [],

        // Process state getter - returns { state, error?, pending_requests? } or null if no active process
        getProcessState: (state) => (sessionId) => state.processStates[sessionId] || null,

        // Effective context max for a session — provider-specific rules (such
        // as Claude Code's auto-promote-to-1M when usage > 85% of the 200K
        // window) live in the provider helpers. Single source of truth used by
        // the settings selector, the header progress ring, and the value sent
        // to the backend so they stay in sync. ``overrideModel`` lets callers
        // preview the value for a model not yet persisted on the session.
        getEffectiveContextMax: (state) => (sessionId, overrideModel = undefined) => {
            const session = state.sessions[sessionId]
            if (!session) return null
            const helpers = getProviderHelpers(session.provider)
            return helpers ? helpers.getEffectiveContextMax(session, overrideModel) : (session.context_max ?? null)
        },

        /**
         * Whether a stop request has been sent for this session and we're
         * waiting for the backend to confirm the process has died.
         * Used by UI components to show a spinner / disabled state on stop buttons.
         */
        isSessionStopping: (state) => (sessionId) =>
            state.processStates[sessionId]?.stopping === true,

        // Whether a session has active (non-stopped) streaming blocks
        hasActiveStreaming: (state) => (sessionId) => {
            const streaming = state.localState.streamingBlocks[sessionId]
            return streaming?.blocks.some(b => !b.stopped) ?? false
        },

        // Pending requests getter - returns an array (oldest first) of pending requests,
        // or an empty array if none. Multiple permission asks can be concurrent within
        // a single session (parallel concurrency-safe tools like Read + Glob).
        getPendingRequests: (state) => (sessionId) =>
            state.processStates[sessionId]?.pending_requests || [],

        /**
         * Count sessions with unread content in a project.
         * A session is unread when last_new_content_at > last_viewed_at (or last_viewed_at is null).
         * Only counts non-draft, non-archived, non-subagent sessions.
         * If a process is running for the session, only counts when in user_turn.
         * @param {string} projectId - The project ID
         * @returns {number} The number of unread sessions
         */
        getProjectUnreadCount: (state) => (projectId) => {
            if (hasActiveStartupPhase(state.startupProgress)) return 0
            let count = 0
            for (const session of Object.values(state.sessions)) {
                if (session.project_id !== projectId) continue
                if (session.draft || session.archived || session.parent_session_id) continue
                if (!session.last_new_content_at) continue
                if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) continue
                // If process is running, only count when in user_turn
                const processState = state.processStates[session.id]
                if (processState && processState.state !== 'user_turn') continue
                count++
            }
            return count
        },

        /**
         * Whether any session globally is in assistant_turn state.
         * Used by the dynamic favicon to show a blue activity dot.
         * @returns {boolean}
         */
        hasGlobalAssistantTurn: (state) => {
            for (const processState of Object.values(state.processStates)) {
                if (processState.state === 'assistant_turn') return true
            }
            return false
        },

        /**
         * Whether at least one session of the given provider has a live (non-dead) process.
         * Dead processes are removed from processStates entirely, so any entry means alive.
         * Used by the Settings panel to prevent disabling a provider that is still in use.
         * @returns {function(string): boolean}
         */
        hasActiveSessionForProvider: (state) => (provider) => {
            for (const ps of Object.values(state.processStates)) {
                if (ps?.provider === provider) return true
            }
            return false
        },

        /**
         * Return the lifecycle state of the given provider, falling back to
         * ``'stopped'`` if absent from the map (a provider missing from the
         * snapshot is treated as not yet started, never as undefined).
         */
        getProviderState: (state) => (provider) => state.providerStates[provider] || 'stopped',

        /**
         * Whether the given provider is usable for runtime calls right now.
         * Combines the intent layer (settings store: enabledProviders) with
         * the runtime layer (this store: providerStates === 'running').
         * Used by every UI surface that pilots a provider's SDK/agent:
         * the in-session callout, the agent-settings picker, the rename
         * action, etc. Choices-of-intent UI (default-provider select,
         * settings sections) keep using settings.enabledProviders directly.
         */
        isProviderAvailable() {
            return (provider) => {
                if (!provider) return false
                const settings = useSettingsStore()
                return settings.enabledProviders.includes(provider)
                    && this.getProviderState(provider) === 'running'
            }
        },

        /**
         * Count sessions with unread content across all projects.
         * Same logic as getProjectUnreadCount but without project filter.
         * @returns {number} The number of unread sessions
         */
        getGlobalUnreadCount: (state) => {
            if (hasActiveStartupPhase(state.startupProgress)) return 0
            let count = 0
            for (const session of Object.values(state.sessions)) {
                if (session.draft || session.archived || session.parent_session_id) continue
                if (!session.last_new_content_at) continue
                if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) continue
                // If process is running, only count when in user_turn
                const processState = state.processStates[session.id]
                if (processState && processState.state !== 'user_turn') continue
                count++
            }
            return count
        },

        // Startup progress getters — aggregate per-phase across every
        // provider that has reported progress. ``current`` and ``total``
        // are summed; ``completed`` is true only when every provider in
        // the phase has reported completion.
        initialSyncProgress: (state) => aggregatePhase(state.startupProgress.initial_sync),
        backgroundComputeProgress: (state) => aggregatePhase(state.startupProgress.background_compute),
        searchIndexProgress: (state) => aggregatePhase(state.startupProgress.search_index),
        isStartupInProgress: (state) => hasActiveStartupPhase(state.startupProgress),
        isInitialSyncInProgress: (state) => {
            const byProvider = state.startupProgress.initial_sync
            if (!byProvider) return false
            return Object.values(byProvider).some(p => p && !p.completed)
        },

        // Local state getters - loading
        isProjectsListLoading: (state) => state.localState.projectsList.loading,
        areSessionsLoading: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsLoading ?? false,
        areSessionItemsLoading: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsLoading ?? false,

        // Local state getters - errors
        didProjectsListFailToLoad: (state) => state.localState.projectsList.loadingError,
        didSessionsFailToLoad: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsLoadingError ?? false,
        didSessionItemsFailToLoad: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsLoadingError ?? false,

        // Local state getters - fetched
        areProjectSessionsFetched: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsFetched ?? false,
        areAllProjectsSessionsFetched: (state) =>
            state.localState.projects[ALL_PROJECTS_ID]?.sessionsFetched ?? false,
        areSessionItemsFetched: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsFetched ?? false,

        // Local state getters - pagination
        hasMoreSessions: (state) => (projectId) =>
            state.localState.projects[projectId]?.hasMoreSessions ?? true,

        // Get expanded groups for a session (returns array)
        getExpandedGroups: (state) => (sessionId) =>
            state.localState.sessionExpandedGroups[sessionId] || [],

        // Check if a group is expanded
        isGroupExpanded: (state) => (sessionId, groupHeadLineNum) => {
            const groups = state.localState.sessionExpandedGroups[sessionId]
            return groups ? groups.includes(groupHeadLineNum) : false
        },

        // Get expanded internal groups for a specific item in a session
        getInternalExpandedGroups: (state) => (sessionId, lineNum) => {
            const sessionGroups = state.localState.sessionInternalExpandedGroups[sessionId]
            if (!sessionGroups) return []
            return sessionGroups[lineNum] || []
        },

        // Check if an internal group is expanded
        isInternalGroupExpanded: (state) => (sessionId, lineNum, startIndex) => {
            const sessionGroups = state.localState.sessionInternalExpandedGroups[sessionId]
            if (!sessionGroups) return false
            const itemGroups = sessionGroups[lineNum]
            return itemGroups ? itemGroups.includes(startIndex) : false
        },

        // Get a single item by lineNum (handles 1-based to 0-based conversion)
        getSessionItem: (state) => (sessionId, lineNum) => {
            const items = state.sessionItems[sessionId]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },

        // Get visual items for a session
        getSessionVisualItems: (state) => (sessionId) =>
            state.localState.sessionVisualItems[sessionId] || [],

        // Check if a conversation block is in detailed mode
        isBlockDetailed: (state) => (sessionId, userMessageLineNum) => {
            const blocks = state.localState.sessionDetailedBlocks[sessionId]
            return blocks ? blocks.includes(userMessageLineNum) : false
        },

        // Get open tabs for a session
        getSessionOpenTabs: (state) => (sessionId) =>
            state.localState.sessionOpenTabs[sessionId] || null,

        // Get cached agent link for a tool_id in a session
        // Returns: { agentId, isBackground } or undefined (not in cache)
        getAgentLink: (state) => (sessionId, toolId) => {
            const sessionLinks = state.localState.agentLinks[sessionId]
            if (!sessionLinks) return undefined
            return sessionLinks[toolId]
        },

        /** Reverse lookup: find the agent link in the parent session that spawned a given subagent. */
        getAgentLinkByAgentId: (state) => (parentSessionId, subagentSessionId) => {
            const sessionLinks = state.localState.agentLinks[parentSessionId]
            if (!sessionLinks) return null
            for (const link of Object.values(sessionLinks)) {
                if (link.agentId === subagentSessionId) return link
            }
            return null
        },

        /** Reverse lookup: find the tool_use line number in the parent session that spawned a given subagent. */
        getAgentToolUseLineNum: (state) => (parentSessionId, subagentSessionId) => {
            const sessionLinks = state.localState.agentLinks[parentSessionId]
            if (!sessionLinks) return null
            for (const link of Object.values(sessionLinks)) {
                if (link.agentId === subagentSessionId) return link.toolUseLineNum ?? null
            }
            return null
        },

        // Get tool state for a tool_use_id in a session
        // Returns: { resultCount, completedAt, error, extra, toolResultLineNums } or null
        getToolState: (state) => (sessionId, toolUseId) => {
            const sessionStates = state.localState.toolStates[sessionId]
            if (!sessionStates) return null
            return sessionStates[toolUseId] || null
        },

        // Check if an item arrived via WebSocket (live, real-time)
        isItemLive: (state) => (sessionId, lineNum) => {
            return !!state.localState.liveItems[sessionId]?.has(lineNum)
        },

        // Check if a wa-details panel is open (persisted across virtual scroller cycles)
        isDetailOpen: (state) => (sessionId, key) => {
            return !!state.localState.openDetails[sessionId]?.[key]
        },

        // Get draft message for a session
        getDraftMessage: (state) => (sessionId) =>
            state.localState.draftMessages[sessionId] || null,

        // Get stored title suggestion for a session
        getTitleSuggestion: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId]?.suggestion || null,

        // Get the full title suggestion entry (to distinguish "no response yet" from "failed")
        getTitleSuggestionEntry: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId] || null,

        // Get the source prompt used for a suggestion (for draft invalidation)
        getTitleSuggestionSourcePrompt: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId]?.sourcePrompt || null,

        // Get attachments for a session as an array (preserving order from Map)
        getAttachments: (state) => (sessionId) => {
            const map = state.localState.attachments[sessionId]
            return map ? Array.from(map.values()) : []
        },

        // Get attachment count for a session
        getAttachmentCount: (state) => (sessionId) => {
            const map = state.localState.attachments[sessionId]
            return map ? map.size : 0
        },

        // Whether any files are currently being processed (encoded/resized) for a session
        isProcessingAttachments: (state) => (sessionId) => {
            return (state.localState.processingAttachments[sessionId] || 0) > 0
        },

        // Get display name for a project (uses cache, computes if missing)
        getProjectDisplayName: (state) => (projectId) => {
            // Return from cache if available
            if (state.localState.projectDisplayNames[projectId]) {
                return state.localState.projectDisplayNames[projectId]
            }

            // Compute and cache
            const project = state.projects[projectId]
            if (!project) return projectId // Fallback to raw ID if project not loaded

            let displayName

            if (project.name) {
                // 1. User-defined name takes priority
                displayName = project.name
            } else if (project.directory) {
                // 2. Last part of directory path
                const parts = project.directory.split('/')
                displayName = parts[parts.length - 1] || project.directory
            } else {
                // 3. Last part of ID after dashes
                const parts = project.id.split('-')
                displayName = parts[parts.length - 1] || project.id
            }

            // Cache it
            state.localState.projectDisplayNames[projectId] = displayName
            return displayName
        }
    },

    actions: {
        // Provider lifecycle state — written from bootstrap (snapshot) and
        // from `provider_state_changed` WS pushes (live transitions).
        applyProviderStates(providerStates) {
            if (!providerStates || typeof providerStates !== 'object') return
            this.providerStates = { ...providerStates }
        },
        setProviderState(provider, state) {
            if (!provider || !state) return
            this.providerStates = { ...this.providerStates, [provider]: state }
        },

        // Server info
        setCurrentVersion(version) {
            this.currentVersion = version
        },
        setPreviousChangelogVersion(version) {
            this.previousChangelogVersion = version
        },
        setPendingChangelogVersion(version) {
            this.pendingChangelogVersion = version
        },
        clearPendingChangelogVersion() {
            this.pendingChangelogVersion = null
        },
        setLatestVersion(version, releaseUrl) {
            this.latestVersion = { version, releaseUrl }
        },

        // Startup progress
        setStartupProgress(provider, phase, current, total, completed) {
            const key = provider ?? '__global__'
            this.startupProgress = {
                ...this.startupProgress,
                [phase]: {
                    ...(this.startupProgress[phase] || {}),
                    [key]: { current, total, completed },
                },
            }
        },

        // Projects
        addProject(project) {
            this.$patch({ projects: { [project.id]: project } })
            // Invalidate display name cache so it gets recomputed
            delete this.localState.projectDisplayNames[project.id]
        },
        updateProject(project) {
            // $patch does a deep merge: only modified props trigger a re-render
            this.$patch({ projects: { [project.id]: project } })
            // Invalidate display name cache so it gets recomputed
            delete this.localState.projectDisplayNames[project.id]
        },
        /**
         * Set the archived state of a project.
         * @param {string} projectId - The project ID
         * @param {boolean} archived - Whether to archive or unarchive
         * @throws {Error} If the update fails
         */
        async setProjectArchived(projectId, archived) {
            // Optimistic update
            const project = this.projects[projectId]
            const oldArchived = project?.archived

            if (project) {
                project.archived = archived
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ archived })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update project')
                }

                const updatedProject = await response.json()
                this.$patch({ projects: { [projectId]: updatedProject } })

            } catch (error) {
                // Rollback on error
                if (project && oldArchived !== undefined) {
                    project.archived = oldArchived
                }
                throw error
            }
        },

        // Sessions
        addSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
            this.tryFinalizePendingBinding(session.id)
        },
        updateSession(session) {
            // When lifecycle timestamps change, clean up stale synthetic process states
            // for child agents that predate the new cutoff
            const prev = this.sessions[session.id]
            if (prev && (prev.last_started_at !== session.last_started_at ||
                         prev.last_stopped_at !== session.last_stopped_at)) {
                this._cleanStaleChildSynthetics(session)
            }
            // Never let last_new_content_at regress — an optimistic value (set when
            // process_state exits assistant_turn) can be overwritten by a stale
            // session_updated broadcast from the file watcher.
            if (prev?.last_new_content_at && session.last_new_content_at &&
                session.last_new_content_at < prev.last_new_content_at) {
                session = { ...session, last_new_content_at: prev.last_new_content_at }
            }
            this.$patch({ sessions: { [session.id]: session } })
            this.tryFinalizePendingBinding(session.id)
        },
        /**
         * Create a draft session for a project.
         * Draft sessions exist only in the frontend until the first message is sent.
         * @param {string} projectId - The project ID
         * @returns {string} The generated session ID (UUID)
         */
        createDraftSession(projectId) {
            const id = generateUUID()
            const now = Date.now() / 1000  // Unix timestamp in seconds
            const provider = useSettingsStore().defaultProvider
            this.sessions[id] = {
                id,
                project_id: projectId,
                provider,
                title: null,  // null = user hasn't set a title yet, UI will display "New session"
                mtime: now,
                last_line: 0,
                draft: true,
            }
            // Persist to IndexedDB
            saveDraftSession(id, { projectId, provider }).catch(err =>
                console.warn('Failed to save draft session to IndexedDB:', err)
            )
            return id
        },

        /**
         * Change the provider of an existing draft session.
         * No-op if the session is not a draft. Persists the new provider to
         * IndexedDB so it survives reloads. Caller is responsible for resetting
         * the per-session agent settings (selected_*) so they follow the new
         * provider's defaults.
         * @param {string} sessionId
         * @param {string} provider - Wire key of the new provider
         */
        setDraftProvider(sessionId, provider) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return
            if (session.provider === provider) return
            session.provider = provider
            saveDraftSession(sessionId, {
                projectId: session.project_id,
                title: session.title,
                provider,
            }).catch(err =>
                console.warn('Failed to save draft session provider to IndexedDB:', err)
            )
        },

        /**
         * Delete a draft session from IndexedDB, and optionally from store.
         * Only deletes if the session exists and has draft: true.
         * @param {string} sessionId - The session ID to delete
         * @param {Object} options - Options
         * @param {boolean} options.keepInStore - If true, only delete from IndexedDB (keep in store)
         */
        deleteDraftSession(sessionId, { keepInStore = false } = {}) {
            if (this.sessions[sessionId]?.draft) {
                if (!keepInStore) {
                    delete this.sessions[sessionId]
                }
                this.removeMruSession(sessionId)
                // Delete from IndexedDB
                deleteDraftSessionFromDb(sessionId).catch(err =>
                    console.warn('Failed to delete draft session from IndexedDB:', err)
                )
            }
        },

        /**
         * Bind a local draft session to its canonical id, once both the
         * `session_bound` WS message and the canonical session itself are
         * available in the store. Decisions are taken at this point, not when
         * `session_bound` arrived: if the user has navigated away from the
         * draft in the meantime, no redirect happens.
         *
         * No-op when `draftId === sessionId`: the provider accepted the
         * client-supplied id (Claude Code) and the existing `session_updated`
         * path will upgrade the draft entry in place.
         *
         * @param {string} draftId - The local draft session id (URL key).
         * @param {string} sessionId - The provider's canonical session id.
         */
        async bindDraftSession(draftId, sessionId) {
            delete this.localState.pendingDraftBindings[draftId]

            if (draftId === sessionId) {
                return
            }

            // Carry the optimistic user message over to the canonical key so
            // it stays visible across the router.replace below. Without this
            // migration, the bubble would disappear the moment the URL flips
            // (optimisticMessages is keyed by sessionId, so the draft entry
            // becomes orphan) and reappear only when the watcher catches the
            // real user_message from the JSONL — a short flicker we want to
            // avoid. ``addSessionItems`` clears the entry as soon as the real
            // message arrives, so this is purely about closing the gap.
            const optimistic = this.localState.optimisticMessages[draftId]
            if (optimistic) {
                this.localState.optimisticMessages[sessionId] = optimistic
                delete this.localState.optimisticMessages[draftId]
                this.recomputeVisualItems(sessionId)
            }

            // Same rekey for an already-arrived title suggestion. The
            // ``suggest_title`` request was sent under the draft id (only id
            // known at request time), so a fast response can have landed in
            // ``titleSuggestions[draftId]`` before this bind runs. Move it to
            // the canonical key so the SessionView watcher — which queries by
            // canonical id after the router.replace below — picks it up.
            const titleSuggestion = this.localState.titleSuggestions[draftId]
            if (titleSuggestion) {
                this.localState.titleSuggestions[sessionId] = titleSuggestion
                delete this.localState.titleSuggestions[draftId]
            }

            // Same migration for an in-flight auto-apply intent — the
            // App-level watcher is observing the draft id at the moment of
            // bind, so the entry must follow the session to its canonical id.
            const pendingAuto = this.localState.pendingTitleAutoApply[draftId]
            if (pendingAuto) {
                this.localState.pendingTitleAutoApply[sessionId] = pendingAuto
                delete this.localState.pendingTitleAutoApply[draftId]
            }

            // Late ``title_suggested`` messages may still arrive with the
            // draft id long after this bind has finished, so register a
            // forwarding alias that ``handleTitleSuggested`` will resolve.
            this.localState.draftAliases[draftId] = sessionId

            const { router } = await import('../router')
            const onDraft = router.currentRoute.value.params.sessionId === draftId

            if (onDraft) {
                const currentRoute = router.currentRoute.value
                await router.replace({
                    name: currentRoute.name,
                    params: { ...currentRoute.params, sessionId },
                    query: currentRoute.query,
                })
            }

            this.deleteDraftSession(draftId)
        },

        /**
         * Check whether a pending draft binding targets the given session id
         * and, if so, finalize it. Called from `addSession` / `updateSession`
         * after the store patch so the canonical session is already visible
         * to `bindDraftSession`.
         * @param {string} sessionId - The session that just landed in the store.
         */
        tryFinalizePendingBinding(sessionId) {
            const pending = this.localState.pendingDraftBindings
            for (const draftId of Object.keys(pending)) {
                if (pending[draftId] === sessionId) {
                    this.bindDraftSession(draftId, sessionId)
                    return
                }
            }
        },

        /**
         * Initialize session items array with placeholders.
         * Placeholders are objects with only line_num (no content).
         * @param {string} sessionId
         * @param {number} lastLine - Total number of lines (session.last_line)
         */
        initSessionItems(sessionId, lastLine) {
            if (this.sessionItems[sessionId]) return // Already initialized

            this.sessionItems[sessionId] = Array.from(
                { length: lastLine },
                (_, index) => ({ line_num: index + 1 }) // line_num is 1-based
            )
        },

        /**
         * Add or update session items in the array.
         * Items are placed at their correct index (line_num - 1).
         * If items arrive beyond current array size, extends with placeholders.
         * @param {string} sessionId
         * @param {Array<{line_num: number, content: string}>} newItems
         * @param {Array<{line_num: number, display_level: number, group_head: number|null, group_tail: number|null, kind: string|null}>|null} updatedMetadata - Metadata of pre-existing items that were modified
         */
        addSessionItems(sessionId, newItems, updatedMetadata = null) {
            let targetArray = this.sessionItems[sessionId]

            // First, apply metadata updates to pre-existing items
            if (updatedMetadata?.length && targetArray) {
                for (const update of updatedMetadata) {
                    const index = update.line_num - 1
                    const existingItem = targetArray[index]
                    if (!existingItem) continue

                    // For user_message or assistant_message that acquires a group_tail,
                    // check if we need to migrate internal suffix expansion to external group
                    if (existingItem.kind === 'user_message' || existingItem.kind === 'assistant_message') {
                        const hadGroupTail = existingItem.group_tail != null
                        const willHaveGroupTail = update.group_tail != null
                        if (!hadGroupTail && willHaveGroupTail && hasContent(existingItem)) {
                            this._migrateInternalSuffixToExternal(sessionId, update.line_num, existingItem)
                        }
                    }

                    // Apply all metadata fields
                    existingItem.display_level = update.display_level
                    existingItem.group_head = update.group_head
                    existingItem.group_tail = update.group_tail
                    existingItem.kind = update.kind
                }
            }

            // Then add new items
            if (!newItems?.length) {
                // Even with no new items, metadata updates may require recompute
                if (updatedMetadata?.length) {
                    this.recomputeVisualItems(sessionId)
                }
                return
            }

            if (!targetArray) {
                // Not initialized yet - create array from the items we have
                // Find max line_num to know array size
                const maxLineNum = Math.max(...newItems.map(item => item.line_num))
                targetArray = this.sessionItems[sessionId] = Array.from(
                    { length: maxLineNum },
                    (_, index) => ({ line_num: index + 1 })
                )
            }

            for (const item of newItems) {
                const index = item.line_num - 1 // line_num is 1-based, array is 0-based

                // Extend array with placeholders if needed
                while (targetArray.length <= index) {
                    targetArray.push({ line_num: targetArray.length + 1 })
                }

                // Place item at correct index
                targetArray[index] = item
            }

            // Clear optimistic message when a real user_message arrives from the backend
            if (this.localState.optimisticMessages[sessionId] &&
                newItems.some(item => item.kind === 'user_message')) {
                delete this.localState.optimisticMessages[sessionId]
            }

            // Retire streaming blocks whose real items have arrived
            this._retireStreamingBlocks(sessionId, newItems)

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Migrate internal suffix expansion state to external group expansion.
         *
         * When an ALWAYS item with an internal suffix acquires a group_tail (because
         * a COLLAPSIBLE item arrived after it), the suffix becomes external.
         * If the user had expanded that internal suffix, we need to migrate
         * that expansion state to the session-level expanded groups.
         *
         * @param {string} sessionId
         * @param {number} lineNum - The line_num of the ALWAYS item
         * @param {Object} item - The session item object
         * @private
         */
        _migrateInternalSuffixToExternal(sessionId, lineNum, item) {
            // Check if there are any internal expanded groups for this item
            const itemInternalGroups = this.localState.sessionInternalExpandedGroups[sessionId]?.[lineNum]
            if (!itemInternalGroups?.length) return

            // Parse content to find the suffix boundaries
            const parsed = getParsedContent(item)
            if (!parsed) return

            const content = parsed?.message?.content
            if (!Array.isArray(content) || content.length === 0) return

            // Use getPrefixSuffixBoundaries with groupTail=true to find where suffix would start
            // (we pass a truthy value for groupTail since we're checking what WILL become external)
            const { suffixStartIndex } = getPrefixSuffixBoundaries(content, null, true)
            if (suffixStartIndex == null) return

            // Check if the suffix was expanded as an internal group
            if (itemInternalGroups.includes(suffixStartIndex)) {
                // Migrate: add to session-level expanded groups
                if (!this.localState.sessionExpandedGroups[sessionId]) {
                    this.localState.sessionExpandedGroups[sessionId] = []
                }
                if (!this.localState.sessionExpandedGroups[sessionId].includes(lineNum)) {
                    this.localState.sessionExpandedGroups[sessionId].push(lineNum)
                }

                // Remove from internal groups
                const idx = itemInternalGroups.indexOf(suffixStartIndex)
                if (idx >= 0) {
                    itemInternalGroups.splice(idx, 1)
                }
            }
        },

        // Initial loading from API

        /**
         * Load all projects from the API.
         * @param {Object} options
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         * @returns {Promise<Set<string>>} Set of project IDs that have changed
         *          (projects where sessionsFetched=true AND mtime changed or new)
         */
        async loadProjects({ isInitialLoading = false } = {}) {
            const changedIds = new Set()
            this.localState.projectsList.loading = true
            try {
                const res = await apiFetch('/api/projects/')
                if (!res.ok) {
                    console.error('Failed to load projects:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.projectsList.loadingError = true
                    }
                    return changedIds
                }
                const freshProjects = await res.json()
                for (const fresh of freshProjects) {
                    const local = this.projects[fresh.id]
                    const wasSessionsFetched = this.localState.projects[fresh.id]?.sessionsFetched

                    // Project changed if: sessionsFetched AND (new OR mtime different)
                    if (wasSessionsFetched && (!local || local.mtime !== fresh.mtime)) {
                        changedIds.add(fresh.id)
                    }

                    // Update store
                    this.projects[fresh.id] = fresh
                }
                // Success: clear any previous error
                this.localState.projectsList.loadingError = false
                return changedIds
            } catch (error) {
                console.error('Failed to load projects:', error)
                if (isInitialLoading) {
                    this.localState.projectsList.loadingError = true
                }
                throw error  // Re-throw for reconciliation retry logic
            } finally {
                this.localState.projectsList.loading = false
            }
        },
        /**
         * Load home page data: projects with weekly activity.
         * Calls /api/home/ which returns projects and weekly activity in one request.
         * Weekly activity is stored separately in weeklyActivity (not on project objects).
         */
        async loadHomeData() {
            // Only show loading indicator on initial load, not on background
            // refreshes (e.g. startup polling) — otherwise the project list
            // flashes away and back on every poll tick.
            const isInitialLoad = Object.keys(this.projects).length === 0
            if (isInitialLoad) {
                this.localState.projectsList.loading = true
            }
            try {
                const res = await apiFetch('/api/home/')
                if (!res.ok) {
                    console.error('Failed to load home data:', res.status, res.statusText)
                    if (isInitialLoad) {
                        this.localState.projectsList.loadingError = true
                    }
                    return
                }
                const data = await res.json()

                // Update projects and weekly activity (strip weekly_activity
                // from project objects, compare before updating to avoid
                // unnecessary re-renders of chart components).
                for (const fresh of data.projects) {
                    const { weekly_activity, ...projectData } = fresh
                    this.projects[projectData.id] = projectData
                    const activity = weekly_activity || []
                    if (JSON.stringify(activity) !== JSON.stringify(this.weeklyActivity[projectData.id])) {
                        this.weeklyActivity[projectData.id] = activity
                    }
                }

                // Store global weekly activity (compare before updating)
                const globalActivity = data.global_weekly_activity || []
                if (JSON.stringify(globalActivity) !== JSON.stringify(this.weeklyActivity._global)) {
                    this.weeklyActivity._global = globalActivity
                }

                this.localState.projectsList.loadingError = false
            } catch (error) {
                console.error('Failed to load home data:', error)
                if (isInitialLoad) {
                    this.localState.projectsList.loadingError = true
                }
            } finally {
                if (isInitialLoad) {
                    this.localState.projectsList.loading = false
                }
            }
        },
        /**
         * Ensure localState.projects[projectId] exists with all pagination fields.
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID
         * @returns {Object} The project's local state object
         * @private
         */
        _ensureProjectLocalState(projectId) {
            if (!this.localState.projects[projectId]) {
                this.localState.projects[projectId] = {
                    sessionsFetched: false,
                    sessionsLoading: false,
                    sessionsLoadingError: false,
                    hasMoreSessions: true,
                    oldestSessionMtime: null,
                }
            }
            return this.localState.projects[projectId]
        },

        /**
         * Fetch a page of sessions from the API.
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID
         * @returns {Promise<{sessions: Array, has_more: boolean}>}
         * @private
         */
        async _fetchSessionsPage(projectId) {
            const state = this._ensureProjectLocalState(projectId)

            // Build URL based on project type
            const isMultiProject = projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)
            const baseUrl = isMultiProject
                ? '/api/sessions/'
                : `/api/projects/${projectId}/sessions/`

            // Add cursor if we have one (for pagination)
            const params = new URLSearchParams()
            if (state.oldestSessionMtime != null) {
                params.set('before_mtime', state.oldestSessionMtime)
            }

            // For workspace mode, filter by the workspace's visible project IDs
            if (isWorkspaceProjectId(projectId)) {
                const wsId = extractWorkspaceId(projectId)
                // Lazy import to avoid circular dependency
                const { useWorkspacesStore } = await import('./workspaces')
                const wsStore = useWorkspacesStore()
                const visibleIds = wsStore.getVisibleProjectIds(wsId)
                if (visibleIds.length) {
                    params.set('project_ids', visibleIds.join(','))
                }
            }

            const url = params.toString() ? `${baseUrl}?${params}` : baseUrl
            const res = await apiFetch(url)

            if (!res.ok) {
                throw new Error(`Failed to load sessions: ${res.status}`)
            }

            return await res.json()
        },

        /**
         * Load sessions for a project or all projects (with pagination support).
         * Handles both initial load and "load more" for infinite scroll.
         *
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID for all projects
         * @param {Object} options
         * @param {boolean} options.force - Reset pagination and reload from beginning
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         * @returns {Promise<Set<string>>} Set of session IDs that have changed
         *          (sessions where itemsFetched=true AND mtime changed or new)
         */
        async loadSessions(projectId, { force = false, isInitialLoading = false } = {}) {
            const changedIds = new Set()
            const state = this._ensureProjectLocalState(projectId)

            // Skip if already loading
            if (state.sessionsLoading) {
                return changedIds
            }

            // Skip if fully loaded (unless force)
            if (!force && state.sessionsFetched && !state.hasMoreSessions) {
                return changedIds
            }

            // Reset pagination state if force
            if (force) {
                state.oldestSessionMtime = null
                state.hasMoreSessions = true
            }

            state.sessionsLoading = true

            try {
                const data = await this._fetchSessionsPage(projectId)

                // Merge sessions into store and track changes
                for (const fresh of data.sessions) {
                    const local = this.sessions[fresh.id]
                    const wasItemsFetched = this.localState.sessions[fresh.id]?.itemsFetched

                    // Session changed if: itemsFetched AND (new OR mtime different)
                    if (wasItemsFetched && (!local || local.mtime !== fresh.mtime)) {
                        changedIds.add(fresh.id)
                    }

                    // Update store
                    this.sessions[fresh.id] = fresh
                }

                // Update pagination state
                state.sessionsFetched = true
                state.hasMoreSessions = data.has_more

                // Update cursor (oldest mtime received)
                if (data.sessions.length > 0) {
                    const oldestReceived = Math.min(...data.sessions.map(s => s.mtime))
                    state.oldestSessionMtime = oldestReceived
                }

                state.sessionsLoadingError = false
                return changedIds
            } catch (error) {
                console.error('Failed to load sessions:', error)
                if (isInitialLoading) {
                    state.sessionsLoadingError = true
                }
                throw error  // Re-throw for reconciliation retry logic
            } finally {
                state.sessionsLoading = false
            }
        },
        /**
         * Load all "sticky" sessions across every project into the store. A
         * sticky session is one that the sidebar may need to render even when
         * a different project/workspace is filtered: pinned sessions (any pin
         * mode), sessions with unread content, or sessions that currently have
         * an active Claude SDK process.
         *
         * The single-project `loadSessions(projectId)` call only populates
         * `this.sessions` with sessions belonging to that project, so without
         * this preload a cross-filter session would be missing from the store
         * and invisible to the sidebar. Subsequent updates are covered by the
         * existing `session_updated` / process-state WebSocket broadcasts.
         */
        async loadStickySessions() {
            try {
                const res = await apiFetch('/api/sessions/?pinned=1&unread=1&has_process=1')
                if (!res.ok) {
                    console.error('Failed to load sticky sessions:', res.status, res.statusText)
                    return
                }
                const data = await res.json()
                for (const session of data.sessions) {
                    this.sessions[session.id] = session
                }
            } catch (error) {
                console.error('Failed to load sticky sessions:', error)
            }
        },
        /**
         * Fetch a single session by ID when its project is not known ahead of time.
         * Populates `this.sessions[sessionId]` on success so reactive consumers
         * (SessionView, SessionList fallback) can proceed. Used when opening a
         * session whose project was not pre-loaded via `loadSessions` — e.g. a
         * cross-filter deep link where the URL's projectId is the sidebar filter
         * rather than the session's real project.
         * @param {string} sessionId
         * @returns {Promise<Object|null>} The session object, or null if not found.
         */
        async loadSessionById(sessionId) {
            if (this.sessions[sessionId]) {
                return this.sessions[sessionId]
            }
            try {
                const response = await fetch(`/api/sessions/${sessionId}/`)
                if (response.status === 404) {
                    return null
                }
                if (!response.ok) {
                    throw new Error(`Failed to load session: ${response.status}`)
                }
                const session = await response.json()
                this.sessions[session.id] = session
                return session
            } catch (error) {
                console.error(`Failed to load session ${sessionId}:`, error)
                throw error
            }
        },
        /**
         * Load all items for a session from the API.
         * @param {string} projectId
         * @param {string} sessionId
         * @param {Object} options
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         */
        async loadSessionItems(projectId, sessionId, { isInitialLoading = false } = {}) {
            // Skip if already fetched
            if (this.localState.sessions[sessionId]?.itemsFetched) {
                return
            }
            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Only set loading if isInitialLoading is true (initial load case)
            if (isInitialLoading) {
                this.localState.sessions[sessionId].itemsLoading = true
            }

            try {
                const res = await apiFetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
                if (!res.ok) {
                    console.error('Failed to load session items:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.sessions[sessionId].itemsLoadingError = true
                    }
                    return
                }
                const items = await res.json()
                this.sessionItems[sessionId] = items
                this.clearOptimisticMessageIfMatched(sessionId, items)
                this.localState.sessions[sessionId].itemsFetched = true
                this.localState.sessions[sessionId].itemsLoadingError = false
            } catch (error) {
                console.error('Failed to load session items:', error)
                if (isInitialLoading) {
                    this.localState.sessions[sessionId].itemsLoadingError = true
                }
            } finally {
                this.localState.sessions[sessionId].itemsLoading = false
            }
        },

        /**
         * Load specific ranges of session items.
         * @param {string} projectId
         * @param {string} sessionId
         * @param {Array<number|[number, number|null]>} ranges - Array of ranges (line_num is 1-based):
         *   - number: exact line (e.g., 5)
         *   - [min, max]: range (e.g., [10, 20])
         *   - [min, null]: from min onwards (e.g., [10, null])
         *   - [null, max]: up to max (e.g., [null, 10])
         * @param {string|null} parentSessionId - If provided, this is a subagent request
         */
        async loadSessionItemsRanges(projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return

            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Coerce a value to an integer string ('' if missing/invalid).
            const toIntStr = (v) => {
                if (v == null || v === '') return ''
                const n = Number(v)
                return Number.isInteger(n) ? String(n) : null
            }

            // Build query params
            const params = new URLSearchParams()
            for (const range of ranges) {
                if (typeof range === 'number' || typeof range === 'string') {
                    const s = toIntStr(range)
                    if (s) {
                        params.append('range', s)
                    } else {
                        console.warn('loadSessionItemsRanges: skipping invalid range', range)
                    }
                } else if (Array.isArray(range)) {
                    const [min, max] = range
                    const minStr = toIntStr(min)
                    const maxStr = toIntStr(max)
                    if (minStr === null || maxStr === null || (minStr === '' && maxStr === '')) {
                        console.warn('loadSessionItemsRanges: skipping invalid range', range)
                        continue
                    }
                    params.append('range', `${minStr}:${maxStr}`)
                } else {
                    console.warn('loadSessionItemsRanges: skipping invalid range', range)
                }
            }

            // Refuse to call without any range — would fetch the entire session.
            if ([...params].length === 0) {
                console.error('loadSessionItemsRanges: no valid range provided, aborting', ranges)
                return
            }

            // Build URL (handle subagent case)
            const baseUrl = parentSessionId
                ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
                : `/api/projects/${projectId}/sessions/${sessionId}`

            try {
                const res = await apiFetch(`${baseUrl}/items/?${params}`)
                if (!res.ok) {
                    console.error('Failed to load session items ranges:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.sessions[sessionId].itemsLoadingError = true
                    }
                    return
                }
                const items = await res.json()
                this.addSessionItems(sessionId, items)
                // Success: clear any previous error
                this.localState.sessions[sessionId].itemsLoadingError = false
            } catch (error) {
                console.error('Failed to load session items ranges:', error)
            }
        },

        // Unload actions (for reconciliation failures or cache cleanup)

        /**
         * Unload a session's items data.
         * Resets itemsFetched to false and clears the items array.
         * Does NOT remove the session itself from the store.
         * @param {string} sessionId
         */
        unloadSession(sessionId) {
            if (this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId].itemsFetched = false
                this.localState.sessions[sessionId].itemsLoading = false
            }
            delete this.sessionItems[sessionId]
            delete this.localState.sessionExpandedGroups[sessionId]
            delete this.localState.sessionInternalExpandedGroups[sessionId]
            delete this.localState.sessionVisualItems[sessionId]
            delete this.localState.visualItemCache[sessionId]
            delete this.localState.optimisticMessages[sessionId]
            delete this.localState.agentLinks[sessionId]
            delete this.localState.toolStates[sessionId]
            delete this.localState.liveItems[sessionId]
            delete this.localState.openDetails[sessionId]
            // Remove synthetic process state if this is a subagent
            if (this.processStates[sessionId]?.synthetic) {
                delete this.processStates[sessionId]
            }
            // Remove synthetic process states for all subagents of this session
            for (const [id, ps] of Object.entries(this.processStates)) {
                if (ps.synthetic && this.sessions[id]?.parent_session_id === sessionId) {
                    delete this.processStates[id]
                }
            }
        },

        /**
         * Unload a project's sessions data.
         * Resets sessionsFetched to false, clears all sessions of this project,
         * and unloads all their items.
         * Does NOT remove the project itself from the store.
         * @param {string} projectId
         */
        unloadProject(projectId) {
            // First, unload all sessions of this project
            const sessionsToUnload = Object.values(this.sessions)
                .filter(s => s.project_id === projectId)
                .map(s => s.id)

            for (const sessionId of sessionsToUnload) {
                this.unloadSession(sessionId)
                delete this.sessions[sessionId]
            }

            // Then reset the project's fetch state
            if (this.localState.projects[projectId]) {
                this.localState.projects[projectId].sessionsFetched = false
            }
        },

        // Visual items computation

        /**
         * Recompute visual items for a session based on current mode and expanded groups.
         * Should be called after:
         * - sessionItems changes (metadata loaded, content loaded, new item via WebSocket)
         * - Display mode changes
         * - Group is toggled
         *
         * @param {string} sessionId
         */
        recomputeVisualItems(sessionId) {
            const items = this.sessionItems[sessionId] || []
            if (!items.length && !this.localState.optimisticMessages[sessionId]) {
                this.localState.sessionVisualItems[sessionId] = []
                this.localState.visualItemCache[sessionId] = new Map()
                return
            }

            // Get effective display mode from settings store
            const settingsStore = useSettingsStore()
            const mode = settingsStore.getDisplayMode
            const expandedGroups = this.localState.sessionExpandedGroups[sessionId] || []

            // Detect assistant_turn (used by computeVisualItems for conversation mode
            // filtering, and for the synthetic working assistant message)
            const processState = this.processStates[sessionId]
            const isAssistantTurn = processState?.state === PROCESS_STATE.ASSISTANT_TURN

            let allItems = items || []
            // Append optimistic message if one exists for this session
            const optimistic = this.localState.optimisticMessages[sessionId]
            if (optimistic) {
                allItems = [...allItems, optimistic]
            }

            // Append a synthetic "starting" assistant message when in starting state.
            // Same structure as the working message but with a simpler content.
            const isStarting = processState?.state === PROCESS_STATE.STARTING
            let startingMessage = null
            if (isStarting) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE
                startingMessage = {
                    line_num: lineNum,
                    content: null,
                    kind: 'assistant_message',
                    syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS,
                    group_head: null,
                    group_tail: null,
                }
                setParsedContent(startingMessage, {
                    type: 'assistant',
                    syntheticKind,
                    message: { role: 'assistant', content: [] },
                })
                allItems = allItems === items ? [...items, startingMessage] : [...allItems, startingMessage]
            }

            // Inject streaming blocks as synthetic items (one per active block).
            // Streaming blocks appear BEFORE the working message in the list.
            const streaming = this.localState.streamingBlocks[sessionId]
            const streamingItems = []
            let hasActiveTextStreaming = false
            if (streaming?.blocks.length) {
                const { baseLineNum, kind: streamingSyntheticKind } = SYNTHETIC_ITEM.STREAMING_BLOCK
                // Last displayable item before streaming (used for group inheritance
                // decisions on streaming thinking blocks). Scans backward past
                // DEBUG_ONLY items and items whose metadata isn't computed yet,
                // so we anchor on the last item the user actually sees.
                let lastRealItem = null
                for (let i = items.length - 1; i >= 0; i--) {
                    const dl = items[i].display_level
                    if (dl === DISPLAY_LEVEL.ALWAYS || dl === DISPLAY_LEVEL.COLLAPSIBLE) {
                        lastRealItem = items[i]
                        break
                    }
                }
                for (const block of streaming.blocks) {
                    if (!block.stopped && block.blockType === 'text') hasActiveTextStreaming = true
                    const lineNum = baseLineNum - block.blockIndex
                    const displayText = block.displayedText ?? block.text
                    const contentBlock = block.blockType === 'thinking'
                        ? { type: 'thinking', thinking: displayText, streaming: !block.stopped }
                        : { type: 'text', text: displayText }
                    const meta = getStreamingItemMetadata(block, lastRealItem, lineNum)
                    const streamItem = {
                        line_num: lineNum,
                        content: null,
                        kind: 'assistant_message',
                        syntheticKind: streamingSyntheticKind,
                        display_level: meta.display_level,
                        group_head: meta.group_head,
                        group_tail: meta.group_tail,
                    }
                    setParsedContent(streamItem, {
                        type: 'assistant',
                        syntheticKind: streamingSyntheticKind,
                        message: { role: 'assistant', content: [contentBlock] },
                    })
                    streamingItems.push(streamItem)
                    allItems = allItems === items ? [...items, streamItem] : [...allItems, streamItem]
                }
            }

            // Get detailed blocks for conversation mode (per-block detail toggle).
            // Computed early because the working-message gating below needs to know
            // whether streaming text is actually visible (it's hidden in conversation
            // mode unless the current block is in detailed mode).
            const detailedBlocksArray = this.localState.sessionDetailedBlocks[sessionId] || []
            const detailedBlocks = new Set(detailedBlocksArray)

            // In conversation mode, streaming items are hidden unless the current
            // block (= the latest user_message) is in detailed mode. When streaming
            // is hidden, we keep the working-message visible so the user has a
            // status indicator instead of a blank screen.
            let isCurrentBlockDetailed = false
            if (mode === DISPLAY_MODE.CONVERSATION && detailedBlocks.size > 0) {
                for (let i = items.length - 1; i >= 0; i--) {
                    if (items[i].kind === 'user_message') {
                        isCurrentBlockDetailed = detailedBlocks.has(items[i].line_num)
                        break
                    }
                }
            }
            const streamingTextWillBeVisible = hasActiveTextStreaming && (
                mode !== DISPLAY_MODE.CONVERSATION || isCurrentBlockDetailed
            )

            // Append a synthetic "working" assistant message when in assistant_turn.
            // Hidden when streaming text is actually visible to the user (which
            // depends on mode and detailed-block state).
            // Injected into allItems so computeVisualItems handles it like any other item.
            // computeVisualItems knows to always let synthetic items (line_num < 0) through,
            // even in conversation mode which normally filters assistant messages.
            let workingMessage = null
            if (isAssistantTurn && !streamingTextWillBeVisible) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE

                workingMessage = {
                    line_num: lineNum,
                    content: null,
                    kind: 'assistant_message',
                    syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS,
                    group_head: null,
                    group_tail: null,
                }
                // Whether the tool card the working-message refers to is actually
                // visible on screen. The component drops the parenthesised target
                // when the user can already see the card right above; in modes
                // where tools are hidden by default (simplified groups, conversation
                // blocks), keep the target unless the user has opened the relevant
                // group/block.
                const lastToolVisible = computeLastToolVisible(
                    items,
                    processState?.lastStartedToolId,
                    mode,
                    expandedGroups,
                    isCurrentBlockDetailed,
                )
                setParsedContent(workingMessage, {
                    type: 'assistant',
                    syntheticKind,
                    label: processState?.label || null,
                    tools: processState?.tools || [],
                    lastStartedToolId: processState?.lastStartedToolId || null,
                    lastToolVisible,
                    message: {
                        role: 'assistant',
                        content: []
                    }
                })
                allItems = allItems === items ? [...items, workingMessage] : [...allItems, workingMessage]
            }

            const visualItems = computeVisualItems(allItems, mode, expandedGroups, isAssistantTurn, detailedBlocks)

            // Reorder /compact command before its compact_summary.
            // In the JSONL file, the compact_summary line appears before the /compact command
            // (despite the user typing it first), so we fix the visual order here.
            if (this.sessions[sessionId]?.compacted) {
                for (let i = 0; i < visualItems.length; i++) {
                    if (visualItems[i].kind !== 'compact_summary') continue
                    for (let j = i + 1; j < Math.min(i + 10, visualItems.length); j++) {
                        if (visualItems[j].kind !== 'user_message') continue
                        const parsed = getParsedContent(visualItems[j])
                        const text = parsed?.message?.content
                        if (typeof text === 'string' && text.includes('<command-name>/compact</command-name>')) {
                            const [moved] = visualItems.splice(j, 1)
                            visualItems.splice(i, 0, moved)
                            break
                        }
                        break  // Only check the first user_message after compact_summary
                    }
                }
            }

            // Propagate syntheticKind to visual items for synthetic messages.
            // computeVisualItems doesn't know about syntheticKind, so we add it here.
            const streamingLineNums = streamingItems.length
                ? new Set(streamingItems.map(si => si.line_num))
                : null
            for (let i = visualItems.length - 1; i >= 0; i--) {
                const vi = visualItems[i]
                if (vi.lineNum === SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum && optimistic) {
                    vi.syntheticKind = optimistic.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.lineNum && startingMessage) {
                    vi.syntheticKind = startingMessage.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.lineNum && workingMessage) {
                    vi.syntheticKind = workingMessage.syntheticKind
                } else if (streamingLineNums?.has(vi.lineNum)) {
                    vi.syntheticKind = SYNTHETIC_ITEM.STREAMING_BLOCK.kind
                }
                // Synthetic items are always at the end, stop as soon as we hit a real item
                if (vi.lineNum >= 0) break
            }

            // Mark each visual item as start/end of its run (block of consecutive
            // user_message items vs block of consecutive non-user items). The CSS
            // uses these flags (.is-block-start / .is-block-end) to render the
            // top/bottom borders of the visual card without depending on
            // adjacent-sibling selectors over `.virtual-scroller-item`. Stable
            // class assignment avoids layout shifts when the scroller loads/unloads
            // items at the rendered range edges.
            for (let i = 0; i < visualItems.length; i++) {
                const isUser = visualItems[i].kind === 'user_message'
                const prevIsUser = i > 0 ? visualItems[i - 1].kind === 'user_message' : null
                const nextIsUser = i < visualItems.length - 1 ? visualItems[i + 1].kind === 'user_message' : null
                visualItems[i].isBlockStart = i === 0 || isUser !== prevIsUser
                visualItems[i].isBlockEnd = i === visualItems.length - 1 || isUser !== nextIsUser
            }

            // Stabilize visual item references: reuse cached objects when properties
            // haven't changed, so Vue sees the same reference and skips re-render.
            const cache = this.localState.visualItemCache[sessionId] || new Map()
            const newCache = new Map()

            const stableItems = visualItems.map(vi => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    // Properties identical — reuse old reference.
                    // Forward the parsed content from the new computation to the
                    // cached object in case items were re-parsed (e.g. content loaded).
                    const parsed = getParsedContent(vi)
                    if (parsed !== null) setParsedContent(cached, parsed)
                    newCache.set(vi.lineNum, cached)
                    return cached
                }
                // Changed or new item — use the new object.
                // Forward parsed content so it's available on the visual item.
                const parsed = getParsedContent(vi)
                if (parsed !== null) setParsedContent(vi, parsed)
                newCache.set(vi.lineNum, vi)
                return vi
            })

            this.localState.visualItemCache[sessionId] = newCache
            this.localState.sessionVisualItems[sessionId] = stableItems
        },

        /**
         * Recompute visual items for ALL sessions.
         * Called when display mode changes (affects all sessions).
         */
        recomputeAllVisualItems() {
            for (const sessionId of Object.keys(this.sessionItems)) {
                this.recomputeVisualItems(sessionId)
            }
        },

        // Optimistic message actions

        /**
         * Set an optimistic user message for a session.
         * Displayed immediately in the conversation while waiting for the backend
         * to confirm with a real user_message item.
         * @param {string} sessionId
         * @param {string} text - The message text
         * @param {Object} [attachments] - Optional attachments in SDK format
         * @param {Array} [attachments.images] - Image blocks ({ type: 'image', source: {...} })
         * @param {Array} [attachments.documents] - Document blocks ({ type: 'document', source: {...} })
         */
        setOptimisticMessage(sessionId, text, attachments) {
            const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE
            // Store as sessionItem format (snake_case) since it's injected into
            // the items array before computeVisualItems processes it.
            const optimisticItem = {
                line_num: lineNum,
                content: null,
                kind: 'user_message',
                syntheticKind,
                _optimisticCreatedAtMs: Date.now(),
                display_level: DISPLAY_LEVEL.ALWAYS,
                group_head: null,
                group_tail: null
            }
            // The parsed-content shape is provider-specific: each renderer in
            // ``SessionItem.vue`` expects its own native JSONL layout (Claude
            // Code reads ``message.content[]``, Codex reads
            // ``payload.message``). The provider's helpers own that mapping.
            const provider = this.getSession(sessionId)?.provider
            const helpers = getProviderHelpers(provider)
            setParsedContent(
                optimisticItem,
                helpers.buildOptimisticUserMessageContent(text, attachments),
            )
            this.localState.optimisticMessages[sessionId] = optimisticItem
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Clear the optimistic message for a session.
         * Called when the real user_message arrives from the backend.
         * @param {string} sessionId
         */
        clearOptimisticMessage(sessionId) {
            if (this.localState.optimisticMessages[sessionId]) {
                delete this.localState.optimisticMessages[sessionId]
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Clear the optimistic message when an API-loaded user_message matches it.
         *
         * Live WebSocket additions can clear on any new user_message because
         * they are causally tied to fresh backend lines. API loads can include
         * older user_messages, so they use content matching to avoid dropping
         * a just-sent placeholder before its real line has been persisted.
         * @param {string} sessionId
         * @param {Array<Object>} items
         */
        clearOptimisticMessageIfMatched(sessionId, items) {
            const optimistic = this.localState.optimisticMessages[sessionId]
            if (!optimistic || !items?.length) return

            const providerHelpers = getProviderHelpers(this.getSession(sessionId)?.provider)
            if (items.some(item => userMessageMatchesOptimistic(providerHelpers, optimistic, item))) {
                delete this.localState.optimisticMessages[sessionId]
            }
        },

        // Expanded groups actions

        /**
         * Toggle expanded state of a group.
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        toggleExpandedGroup(sessionId, groupHeadLineNum) {
            // Ensure array exists for this session
            if (!this.localState.sessionExpandedGroups[sessionId]) {
                this.localState.sessionExpandedGroups[sessionId] = []
            }

            const groups = this.localState.sessionExpandedGroups[sessionId]
            const index = groups.indexOf(groupHeadLineNum)

            if (index >= 0) {
                // Collapse: remove from array
                groups.splice(index, 1)
            } else {
                // Expand: add to array
                groups.push(groupHeadLineNum)
            }

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Expand a group (idempotent).
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        expandGroup(sessionId, groupHeadLineNum) {
            if (!this.localState.sessionExpandedGroups[sessionId]) {
                this.localState.sessionExpandedGroups[sessionId] = []
            }
            const groups = this.localState.sessionExpandedGroups[sessionId]
            if (!groups.includes(groupHeadLineNum)) {
                groups.push(groupHeadLineNum)
            }
        },

        /**
         * Collapse a group (idempotent).
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        collapseGroup(sessionId, groupHeadLineNum) {
            const groups = this.localState.sessionExpandedGroups[sessionId]
            if (groups) {
                const index = groups.indexOf(groupHeadLineNum)
                if (index >= 0) {
                    groups.splice(index, 1)
                }
            }
        },

        /**
         * Collapse all groups for a session.
         * @param {string} sessionId
         */
        collapseAllGroups(sessionId) {
            this.localState.sessionExpandedGroups[sessionId] = []
        },

        // Detailed blocks actions (conversation mode per-block detail toggle)

        /**
         * Toggle a conversation block between conversation and detailed display mode.
         * @param {string} sessionId
         * @param {number} userMessageLineNum - line_num of the last user_message before the block
         */
        toggleBlockDetailedMode(sessionId, userMessageLineNum) {
            if (!this.localState.sessionDetailedBlocks[sessionId]) {
                this.localState.sessionDetailedBlocks[sessionId] = []
            }

            const blocks = this.localState.sessionDetailedBlocks[sessionId]
            const index = blocks.indexOf(userMessageLineNum)

            if (index >= 0) {
                // Collapse back to conversation mode: remove from array
                blocks.splice(index, 1)
            } else {
                // Expand to detailed mode: add to array
                blocks.push(userMessageLineNum)
            }

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Ensure a conversation block is in detailed mode (expand without toggling).
         * No-op if the block is already expanded.
         * @param {string} sessionId
         * @param {number} userMessageLineNum - line_num of the last user_message before the block
         * @returns {boolean} true if the block was expanded (visual items recomputed)
         */
        ensureBlockDetailed(sessionId, userMessageLineNum) {
            if (!this.localState.sessionDetailedBlocks[sessionId]) {
                this.localState.sessionDetailedBlocks[sessionId] = []
            }

            const blocks = this.localState.sessionDetailedBlocks[sessionId]
            if (blocks.includes(userMessageLineNum)) {
                return false  // Already expanded
            }

            blocks.push(userMessageLineNum)
            this.recomputeVisualItems(sessionId)
            return true
        },

        /**
         * Toggle expanded state of an internal group within an ALWAYS item's content.
         * @param {string} sessionId
         * @param {number} lineNum - line_num of the ALWAYS item containing the group
         * @param {number} startIndex - startIndex of the internal group within content array
         */
        toggleInternalExpandedGroup(sessionId, lineNum, startIndex) {
            // Ensure nested structure exists
            if (!this.localState.sessionInternalExpandedGroups[sessionId]) {
                this.localState.sessionInternalExpandedGroups[sessionId] = {}
            }
            if (!this.localState.sessionInternalExpandedGroups[sessionId][lineNum]) {
                this.localState.sessionInternalExpandedGroups[sessionId][lineNum] = []
            }

            const groups = this.localState.sessionInternalExpandedGroups[sessionId][lineNum]
            const index = groups.indexOf(startIndex)

            if (index >= 0) {
                // Collapse: remove from array
                groups.splice(index, 1)
            } else {
                // Expand: add to array
                groups.push(startIndex)
            }
        },

        /**
         * Load metadata for all items in a session (without content).
         * @param {string} projectId
         * @param {string} sessionId
         * @param {string|null} parentSessionId - If provided, this is a subagent request
         * @returns {Promise<Array|null>} Array of metadata objects or null on error
         */
        async loadSessionMetadata(projectId, sessionId, parentSessionId = null) {
            // Build URL (handle subagent case)
            const baseUrl = parentSessionId
                ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
                : `/api/projects/${projectId}/sessions/${sessionId}`

            try {
                const res = await apiFetch(`${baseUrl}/items/metadata/`)
                if (!res.ok) {
                    console.error('Failed to load session metadata:', res.status, res.statusText)
                    return null
                }
                return await res.json()
            } catch (error) {
                console.error('Failed to load session metadata:', error)
                return null
            }
        },

        /**
         * Initialize sessionItems array from metadata (no content).
         * @param {string} sessionId
         * @param {Array} metadata - Array of { line_num, display_level, group_head, group_tail }
         */
        initSessionItemsFromMetadata(sessionId, metadata) {
            this.sessionItems[sessionId] = metadata.map(m => ({
                line_num: m.line_num,
                display_level: m.display_level,
                group_head: m.group_head,
                group_tail: m.group_tail,
                kind: m.kind,
                content: null  // Will be filled by content fetch
            }))

            // Compute visual items after initialization
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Update existing session items with fetched content.
         * @param {string} sessionId
         * @param {Array} items - Array of { line_num, content, display_level, group_head, group_tail, kind }
         */
        updateSessionItemsContent(sessionId, items) {
            const sessionItemsArray = this.sessionItems[sessionId]
            if (!sessionItemsArray) return

            const updatedItems = []
            for (const item of items) {
                const index = item.line_num - 1  // line_num is 1-based
                if (sessionItemsArray[index]) {
                    // Update content and invalidate parsed content cache
                    sessionItemsArray[index].content = item.content
                    clearParsedContent(sessionItemsArray[index])
                    // Also update metadata in case it was computed after initial load
                    if (item.display_level != null) {
                        sessionItemsArray[index].display_level = item.display_level
                    }
                    if (item.group_head != null) {
                        sessionItemsArray[index].group_head = item.group_head
                    }
                    if (item.group_tail != null) {
                        sessionItemsArray[index].group_tail = item.group_tail
                    }
                    if (item.kind !== undefined) {
                        sessionItemsArray[index].kind = item.kind
                    }
                    updatedItems.push(sessionItemsArray[index])
                }
            }

            this.clearOptimisticMessageIfMatched(sessionId, updatedItems)

            // Recompute visual items in case metadata changed
            this.recomputeVisualItems(sessionId)
        },

        // Tab management actions

        /**
         * Add a tab to a session's open tabs.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The tab ID to add (e.g., 'agent-xxx')
         */
        addSessionTab(sessionId, tabId) {
            if (!this.localState.sessionOpenTabs[sessionId]) {
                this.localState.sessionOpenTabs[sessionId] = {
                    tabs: ['main'],
                    activeTab: 'main'
                }
            }
            const state = this.localState.sessionOpenTabs[sessionId]
            if (!state.tabs.includes(tabId)) {
                state.tabs.push(tabId)
            }
        },

        /**
         * Remove a tab from a session's open tabs.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The tab ID to remove (e.g., 'agent-xxx')
         */
        removeSessionTab(sessionId, tabId) {
            const state = this.localState.sessionOpenTabs[sessionId]
            if (!state) return

            const index = state.tabs.indexOf(tabId)
            if (index > -1) {
                state.tabs.splice(index, 1)
            }
        },

        /**
         * Set the active tab for a session.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The active tab ID
         */
        setSessionActiveTab(sessionId, tabId) {
            if (!this.localState.sessionOpenTabs[sessionId]) {
                this.localState.sessionOpenTabs[sessionId] = {
                    tabs: ['main'],
                    activeTab: 'main'
                }
            }
            this.localState.sessionOpenTabs[sessionId].activeTab = tabId
        },

        /**
         * Clear saved tabs for a session.
         * @param {string} sessionId - The session ID
         */
        clearSessionOpenTabs(sessionId) {
            delete this.localState.sessionOpenTabs[sessionId]
        },

        // Agent links cache actions

        /**
         * Set an agent link in the cache.
         * @param {string} sessionId - The session ID
         * @param {string} toolId - The tool_use_id
         * @param {string} agentId - The agent ID (only cache when found)
         * @param {boolean} isBackground - Whether the agent runs in background
         * @param {?number} toolUseLineNum - Line of the spawning tool_use
         * @param {?string} slug - Spawned subagent's nickname (Codex
         *   ``agent_nickname`` persisted as ``Session.slug``). Joined
         *   into the AgentLink payload at the API / WS boundary so
         *   downstream code can label tab headers / tool-card summaries
         *   without separately hydrating the subagent Session row.
         */
        setAgentLink(sessionId, toolId, agentId, isBackground = false, toolUseLineNum = null, slug = null) {
            if (!agentId) return // Only cache found agents
            if (!this.localState.agentLinks[sessionId]) {
                this.localState.agentLinks[sessionId] = {}
            }
            this.localState.agentLinks[sessionId][toolId] = { agentId, isBackground, toolUseLineNum, slug }
        },

        /**
         * Clear agent links cache for a session.
         * @param {string} sessionId - The session ID
         */
        clearAgentLinks(sessionId) {
            delete this.localState.agentLinks[sessionId]
        },

        /**
         * Set tool state for a tool_use_id in a session.
         * @param {string} sessionId - The session ID
         * @param {string} toolUseId - The tool_use_id
         * @param {number} resultCount - The number of tool_results received
         * @param {string|null} completedAt - ISO timestamp of the latest tool_result
         * @param {string|null} error - Error message if the tool errored
         * @param {string|null} extra - Extra JSON data (e.g., file change stats)
         * @param {number[]} toolResultLineNums - Line numbers of every tool_result row, ordered ASC
         */
        setToolState(sessionId, toolUseId, resultCount, completedAt, error = null, extra = null, toolResultLineNums = []) {
            if (!this.localState.toolStates[sessionId]) {
                this.localState.toolStates[sessionId] = {}
            }
            this.localState.toolStates[sessionId][toolUseId] = { resultCount, completedAt, error, extra, toolResultLineNums }
        },

        /**
         * Mark session items as live (arrived via WebSocket in real-time).
         * @param {string} sessionId - The session ID
         * @param {number[]} lineNums - Line numbers of items that arrived via WebSocket
         */
        markItemsLive(sessionId, lineNums) {
            if (!lineNums?.length) return
            if (!this.localState.liveItems[sessionId]) {
                this.localState.liveItems[sessionId] = new Set()
            }
            for (const ln of lineNums) {
                this.localState.liveItems[sessionId].add(ln)
            }
        },

        /**
         * Fetch tool states for a session from the API.
         * Populates the toolStates cache.
         *
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         */
        async fetchToolStates(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/tool-states/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const data = await response.json()
                if (data.tools && Object.keys(data.tools).length > 0) {
                    const states = {}
                    for (const [toolUseId, state] of Object.entries(data.tools)) {
                        states[toolUseId] = {
                            resultCount: state.result_count,
                            completedAt: state.completed_at,
                            error: state.error ?? null,
                            extra: state.extra ?? null,
                            toolResultLineNums: Array.isArray(state.tool_result_line_nums)
                                ? state.tool_result_line_nums
                                : [],
                        }
                    }
                    this.localState.toolStates[sessionId] = states
                }
            } catch (error) {
                console.error('Failed to fetch tool states:', error)
            }
        },

        // Open details state actions (persisted across virtual scroller mount/unmount)

        /**
         * Set or clear the open state of a wa-details panel.
         * @param {string} sessionId - The session ID
         * @param {string} key - Unique key (toolId, `result:${toolId}`, etc.)
         * @param {boolean} open - Whether the panel is open
         */
        setDetailOpen(sessionId, key, open) {
            if (open) {
                if (!this.localState.openDetails[sessionId]) {
                    this.localState.openDetails[sessionId] = {}
                }
                this.localState.openDetails[sessionId][key] = true
            } else {
                if (this.localState.openDetails[sessionId]) {
                    delete this.localState.openDetails[sessionId][key]
                }
            }
        },

        // Subagent state actions

        /**
         * Set a synthetic process state for a subagent (assistant_turn).
         * Does not overwrite real (non-synthetic) process states.
         * Triggers recomputeVisualItems only if the session's items are loaded
         * and the assistant_turn status actually changed.
         *
         * @param {string} agentSessionId - The subagent session ID
         * @param {string} parentSessionId - The parent session that spawned the subagent
         *   (its ``provider`` is inherited by the synthetic state).
         * @param {string} projectId - The project ID
         * @param {number|null} startedAtUnix - Unix timestamp (seconds) of when the agent started
         */
        setSyntheticProcessState(agentSessionId, parentSessionId, projectId, startedAtUnix) {
            // Don't overwrite real process states (from ProcessManager)
            if (this.processStates[agentSessionId] && !this.processStates[agentSessionId].synthetic) {
                return
            }
            const provider = this.getSessionProvider(parentSessionId)
            if (!provider) {
                console.warn('[setSyntheticProcessState] no provider for parent session', parentSessionId)
                return
            }
            const wasAssistantTurn = this.processStates[agentSessionId]?.state === PROCESS_STATE.ASSISTANT_TURN
            this.processStates[agentSessionId] = {
                state: PROCESS_STATE.ASSISTANT_TURN,
                project_id: projectId,
                provider,
                started_at: startedAtUnix,
                state_changed_at: startedAtUnix,
                memory: null,
                error: null,
                pending_requests: [],
                session_title: null,
                project_name: null,
                synthetic: true,
            }
            if (!wasAssistantTurn && this.sessionItems[agentSessionId]) {
                this.recomputeVisualItems(agentSessionId)
            }
        },

        /**
         * Clean up synthetic process states for child agents that predate the session's
         * lifecycle cutoff (max of last_started_at, last_stopped_at)).
         * Called reactively when session lifecycle timestamps change in updateSession.
         *
         * @param {Object} session - The session object (with last_started_at, last_stopped_at)
         */
        _cleanStaleChildSynthetics(session) {
            const links = this.localState.agentLinks[session.id]
            if (!links) return
            const cutoff = getSessionCutoffMs(session)
            if (!cutoff) return
            for (const { agentId } of Object.values(links)) {
                const ps = this.processStates[agentId]
                if (!ps?.synthetic) continue
                // started_at is in seconds, cutoff in ms
                const startedMs = ps.started_at ? ps.started_at * 1000 : 0
                if (startedMs < cutoff) {
                    this.removeSyntheticProcessState(agentId)
                }
            }
        },

        /**
         * Remove a synthetic process state for a subagent.
         * Only removes if the process state is synthetic (not a real process).
         * Triggers recomputeVisualItems only if the session's items are loaded.
         *
         * @param {string} agentSessionId - The subagent session ID
         */
        removeSyntheticProcessState(agentSessionId) {
            const ps = this.processStates[agentSessionId]
            if (!ps?.synthetic) return
            const wasAssistantTurn = ps.state === PROCESS_STATE.ASSISTANT_TURN
            delete this.processStates[agentSessionId]
            if (wasAssistantTurn && this.sessionItems[agentSessionId]) {
                this.recomputeVisualItems(agentSessionId)
            }
        },

        /**
         * Fetch and set synthetic process states for all subagents of a session.
         * Called at session load time when the session has a process in assistant_turn.
         * Creates synthetic processState entries for agents that are not done.
         *
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The parent session ID
         */
        async fetchSubagentsState(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/subagents/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const agents = await response.json()

                // Cutoff: agents started before this are definitely not running
                const cutoff = getSessionCutoffMs(this.sessions[sessionId])

                for (const agent of agents) {
                    this.setAgentLink(sessionId, agent.tool_use_id, agent.agent_id, agent.is_background, agent.tool_use_line_num, agent.agent_slug ?? null)

                    // Skip synthetic process state if agent predates the session's last start/stop cycle
                    const agentStartedMs = agent.started_at ? new Date(agent.started_at).getTime() : 0
                    if (cutoff && agentStartedMs < cutoff) continue

                    // Create synthetic process state if agent is not done yet
                    const toolState = this.localState.toolStates[sessionId]?.[agent.tool_use_id]
                    const resultCount = toolState?.resultCount || 0
                    const requiredCount = agent.is_background ? 2 : 1
                    if (resultCount < requiredCount) {
                        const startedAtUnix = agent.started_at ? new Date(agent.started_at).getTime() / 1000 : null
                        this.setSyntheticProcessState(agent.agent_id, sessionId, projectId, startedAtUnix)
                    }
                }
            } catch (error) {
                console.error('Failed to fetch subagents state:', error)
            }
        },

        // Process state actions

        /**
         * Mark a session as "stopping" so the UI can reflect it immediately.
         * The flag is automatically cleared when the backend replaces the
         * processState entry (on state transition, including DEAD).
         * No-op if the session has no active process state.
         * @param {string} sessionId
         */
        setSessionStopping(sessionId) {
            const ps = this.processStates[sessionId]
            if (!ps) return
            this.processStates[sessionId] = { ...ps, stopping: true }
        },

        /**
         * Set process state for a session (from WebSocket process_state message).
         * Removes the entry when state is 'dead'.
         * @param {string} sessionId
         * @param {string} projectId - The project ID this session belongs to
         * @param {string} state - 'starting' | 'assistant_turn' | 'user_turn' | 'dead'
         * @param {object} extra - Additional fields: provider, started_at, state_changed_at, memory, error, pending_requests, session_title, project_name
         */
        setProcessState(sessionId, projectId, state, extra = {}) {
            const previousState = this.processStates[sessionId]?.state
            const wasAssistantTurn = previousState === PROCESS_STATE.ASSISTANT_TURN
            const wasStarting = previousState === PROCESS_STATE.STARTING

            if (state === 'dead') {
                // Remove dead processes from the map
                delete this.processStates[sessionId]
                // Clean up any lingering streaming blocks and buffers
                const lingering = this.localState.streamingBlocks[sessionId]
                if (lingering) {
                    for (const block of lingering.blocks) {
                        clearBlockInactivityTimer(block)
                    }
                }
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            } else {
                this.processStates[sessionId] = {
                    state,
                    project_id: projectId,
                    provider: extra.provider || null,
                    started_at: extra.started_at || null,
                    state_changed_at: extra.state_changed_at || null,
                    memory: extra.memory || null,
                    error: extra.error || null,
                    pending_requests: extra.pending_requests || [],
                    active_crons: extra.active_crons || null,
                    session_title: extra.session_title || null,
                    project_name: extra.project_name || null,
                    tools: [],
                    lastStartedToolId: null,
                }

                // Auto-unarchive: running and archived are mutually exclusive
                const session = this.sessions[sessionId]
                if (session?.archived && projectId) {
                    this.setSessionArchived(projectId, sessionId, false)
                }
            }

            // Recompute visual items when isAssistantTurn or isStarting changes
            // (controls the synthetic working/starting messages and conversation mode filtering)
            const isStarting = state === PROCESS_STATE.STARTING
            const isAssistantTurn = state === PROCESS_STATE.ASSISTANT_TURN
            if (wasAssistantTurn !== isAssistantTurn || wasStarting !== isStarting) {
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Initialize process states from WebSocket active_processes message.
         * Called on connection to sync with backend.
         * @param {Array<{session_id: string, project_id: string, state: string, started_at?: number, state_changed_at?: number, memory?: number, session_title?: string, project_name?: string}>} processes
         */
        setActiveProcesses(processes) {
            // Clear existing states and rebuild from server data
            this.processStates = {}
            // Clear stale streaming blocks and buffers from previous connection
            destroyAllBuffers()
            this.localState.streamingBlocks = {}
            for (const p of processes) {
                // Only add non-dead processes
                if (p.state !== 'dead') {
                    this.processStates[p.session_id] = {
                        state: p.state,
                        project_id: p.project_id,
                        provider: p.provider || null,
                        started_at: p.started_at || null,
                        state_changed_at: p.state_changed_at || null,
                        memory: p.memory || null,
                        error: p.error || null,
                        pending_requests: p.pending_requests || [],
                        active_crons: p.active_crons || null,
                        session_title: p.session_title || null,
                        project_name: p.project_name || null,
                        tools: Array.isArray(p.active_tools) ? p.active_tools : [],
                        lastStartedToolId: p.last_started_tool_id || null,
                    }

                    // Auto-unarchive: running and archived are mutually exclusive
                    const session = this.sessions[p.session_id]
                    if (session?.archived && p.project_id) {
                        this.setSessionArchived(p.project_id, p.session_id, false)
                    }
                }
            }
        },

        // ── Streaming blocks ─────────────────────────────────────────────

        /**
         * Handle a stream_block_start event from the SDK.
         * Creates or resets the streaming state for this session/message,
         * then adds the new block entry.
         */
        streamBlockStart(sessionId, messageId, blockIndex, blockType) {
            const existing = this.localState.streamingBlocks[sessionId]
            if (!existing || existing.messageId !== messageId) {
                // New message — start fresh (destroy any old buffers).
                // Before dropping the previous entry we close any of its
                // streaming detailKeys we may have left open: the next
                // synthetic block will land at the same negative ``lineNum``
                // and ``Reasoning.vue`` / ``ThinkingContent.vue`` initialize
                // ``isOpen`` from ``isDetailOpen``, so a stale ``true`` from
                // the previous reasoning would auto-open the next one.
                // ``_retireStreamingBlocks`` does the same reset when the
                // real SessionItem arrives — this branch handles the race
                // where the next stream starts before the previous JSONL
                // line lands (typical for back-to-back Codex reasonings).
                if (existing) {
                    const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
                    for (const oldBlock of existing.blocks) {
                        clearBlockInactivityTimer(oldBlock)
                        this.setDetailOpen(
                            sessionId,
                            `line:${baseLineNum - oldBlock.blockIndex}:0`,
                            false,
                        )
                    }
                }
                destroySessionBuffers(sessionId)
                this.localState.streamingBlocks[sessionId] = {
                    messageId,
                    blocks: [{ blockIndex, blockType, text: '', displayedText: '', stopped: false, uuid: null }],
                }
            } else {
                // Same message, additional block (e.g. thinking then text)
                existing.blocks.push({ blockIndex, blockType, text: '', displayedText: '', stopped: false, uuid: null })
            }

            // Initialize the adaptive buffer for this block
            initBuffer(sessionId, blockIndex, (displayedText) => {
                this._onBufferDrain(sessionId, blockIndex, displayedText)
            })

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Handle a stream_block_delta event — append text to the current block.
         * Feeds the delta into the adaptive buffer which drains it smoothly
         * via requestAnimationFrame, patching the visual item on each frame.
         */
        streamBlockDelta(sessionId, messageId, blockIndex, text) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return

            block.text += text
            feedDelta(sessionId, blockIndex, text)

            // (Re)arm the inactivity timer for text blocks. If the SDK goes
            // quiet for STREAM_BLOCK_INACTIVITY_MS we'll flip ``stopped`` so
            // the WorkingAssistantMessage indicator reappears, even though
            // ``item/completed`` from Codex may still be seconds away. If a
            // delta arrives after the flip, we revert ``stopped`` back to
            // false so the indicator hides again while new content streams.
            // Thinking blocks are excluded: they don't gate the
            // WorkingAssistantMessage (``hasActiveTextStreaming`` only looks
            // at text blocks).
            if (block.blockType === 'text') {
                clearBlockInactivityTimer(block)
                if (block.stopped) {
                    block.stopped = false
                    this.recomputeVisualItems(sessionId)
                }
                block._inactivityTimer = setTimeout(() => {
                    block._inactivityTimer = null
                    if (block.stopped) return
                    block.stopped = true
                    this.recomputeVisualItems(sessionId)
                }, STREAM_BLOCK_INACTIVITY_MS)
            }
        },

        /**
         * Buffer drain callback — patches the streaming visual item with
         * the currently displayed text. Called from requestAnimationFrame
         * by the adaptive buffer.
         * @private
         */
        _onBufferDrain(sessionId, blockIndex, displayedText) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return

            block.displayedText = displayedText

            const { baseLineNum, kind: streamingSyntheticKind } = SYNTHETIC_ITEM.STREAMING_BLOCK
            const targetLineNum = baseLineNum - blockIndex
            const visualItems = this.localState.sessionVisualItems[sessionId]
            if (!visualItems) return

            const idx = visualItems.findIndex(vi => vi.lineNum === targetLineNum)
            if (idx === -1) return

            const contentBlock = block.blockType === 'thinking'
                ? { type: 'thinking', thinking: displayedText, streaming: !block.stopped }
                : { type: 'text', text: displayedText }
            const newParsed = {
                type: 'assistant',
                syntheticKind: streamingSyntheticKind,
                message: { role: 'assistant', content: [contentBlock] },
            }

            const newVi = { ...visualItems[idx] }
            setParsedContent(newVi, newParsed)
            visualItems[idx] = newVi

            const cache = this.localState.visualItemCache[sessionId]
            if (cache) cache.set(targetLineNum, newVi)
        },

        /**
         * Handle a stream_block_stop event — mark the block as stopped (text
         * is final but uuid not yet known). Flushes the buffer then triggers
         * recompute because the WorkingAssistantMessage visibility depends
         * on this flag.
         */
        streamBlockStop(sessionId, messageId, blockIndex) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (block) {
                // Don't flush the buffer here — let it keep draining naturally.
                // The remaining chars will be displayed over the next few hundred ms
                // before the real item arrives and retires the block.
                clearBlockInactivityTimer(block)
                block.stopped = true
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Handle a stream_block_end event — record the uuid so we can match
         * the real SessionItem when it arrives from the watcher.
         *
         * Also handles a race condition: the watcher's session_items_added may
         * arrive BEFORE this end event. In that case, _retireStreamingBlocks
         * already ran but couldn't match (uuid was null). We scan existing
         * session items for a retroactive match.
         */
        streamBlockEnd(sessionId, messageId, blockIndex, uuid) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return
            block.uuid = uuid

            // Retroactive match: the real item may already be in sessionItems
            const items = this.sessionItems[sessionId]
            if (!items) return
            for (let i = items.length - 1; i >= 0; i--) {
                const item = items[i]
                if (item.kind !== 'assistant_message' && item.kind !== 'content_items' && item.kind !== 'reasoning') continue
                // Provider-agnostic uuid path: when the backend stamped a
                // ``stream_uuid`` on the wire item (Codex live-sync), that
                // single field is sufficient — no need to parse content or
                // match message.id. For Claude the uuid lives inside the
                // parsed JSONL ``uuid`` field, gated by ``message.id``.
                if (item.stream_uuid === uuid) {
                    this._retireStreamingBlocks(sessionId, [item])
                    this.recomputeVisualItems(sessionId)
                    return
                }
                const parsed = getParsedContent(item)
                if (!parsed) continue
                if (parsed.message?.id !== messageId) continue
                if (parsed.uuid === uuid) {
                    this._retireStreamingBlocks(sessionId, [item])
                    this.recomputeVisualItems(sessionId)
                    return
                }
            }
        },

        /**
         * Try to retire streaming blocks whose real SessionItem has arrived.
         * Called from addSessionItems after new items are placed in the array.
         *
         * Match strategy (in order):
         *   1. ``item.stream_uuid`` (Codex live-sync) — the backend popped
         *      the streaming registry and stamped the SDK ``item_id`` on
         *      the wire payload. We retire the block whose uuid matches,
         *      no parsed content needed.
         *   2. Otherwise, parse the JSONL ``uuid`` and ``message.id``
         *      (Claude path) and match those against the streaming entry.
         */
        _retireStreamingBlocks(sessionId, newItems) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return

            for (const item of newItems) {
                if (item.kind !== 'assistant_message' && item.kind !== 'content_items' && item.kind !== 'reasoning') continue

                let itemUuid = item.stream_uuid
                let parsed = null
                if (!itemUuid) {
                    parsed = getParsedContent(item)
                    if (!parsed) continue
                    const itemMessageId = parsed.message?.id
                    if (itemMessageId !== streaming.messageId) continue
                    itemUuid = parsed.uuid
                    if (!itemUuid) continue
                }

                // Find and remove the matching block
                const idx = streaming.blocks.findIndex(b => b.uuid === itemUuid)
                if (idx !== -1) {
                    const block = streaming.blocks[idx]

                    // Transfer wa-details open state from streaming to real item
                    if (block.blockType === 'thinking') {
                        // Lazy parse: Codex went through the stream_uuid
                        // short-circuit and ``parsed`` may still be null. Claude
                        // already had it loaded by the parent loop.
                        if (!parsed) parsed = getParsedContent(item)
                        const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
                        const streamingDetailKey = `line:${baseLineNum - block.blockIndex}:0`
                        if (this.isDetailOpen(sessionId, streamingDetailKey)) {
                            // The real item's detailKey depends on the provider:
                            //   - Codex: the JSONL line itself *is* the reasoning
                            //     item (kind=reasoning), mono-block, ``:0`` suffix.
                            //   - Claude: the thinking sits inside an
                            //     assistant_message's content array; look up its
                            //     position to build ``line:${lineNum}:${idx}``.
                            let targetKey = null
                            if (item.kind === 'reasoning') {
                                targetKey = `line:${item.line_num}:0`
                            } else {
                                const content = parsed?.message?.content
                                if (Array.isArray(content)) {
                                    const thinkingIdx = content.findIndex(c => c.type === 'thinking')
                                    if (thinkingIdx !== -1) {
                                        targetKey = `line:${item.line_num}:${thinkingIdx}`
                                    }
                                }
                            }
                            if (targetKey) {
                                this.setDetailOpen(sessionId, targetKey, true)
                            }
                            this.setDetailOpen(sessionId, streamingDetailKey, false)
                        }
                        // Transfer expandedGroups state for fake-group case.
                        // If this thinking block was its own group_head (fake group)
                        // and the user expanded it, migrate that entry to the real
                        // item's group_head so expansion persists across the swap.
                        const expanded = this.localState.sessionExpandedGroups[sessionId]
                        if (expanded && expanded.length > 0) {
                            const streamingLineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex
                            const idxInExpanded = expanded.indexOf(streamingLineNum)
                            if (idxInExpanded !== -1) {
                                // Determine the real group_head this thinking block
                                // belongs to. Look at the real item's group_head; if
                                // null (no group), drop the entry; else add the real
                                // group_head if not already there.
                                const realGroupHead = item.group_head
                                expanded.splice(idxInExpanded, 1)
                                if (realGroupHead != null && !expanded.includes(realGroupHead)) {
                                    expanded.push(realGroupHead)
                                }
                            }
                        }
                    }

                    clearBlockInactivityTimer(block)
                    flushBuffer(sessionId, block.blockIndex)
                    streaming.blocks.splice(idx, 1)
                }
            }

            // If all blocks retired, clean up
            if (streaming.blocks.length === 0) {
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            }
        },

        /**
         * Drop streaming blocks that already ended (``uuid`` set) but were
         * never retired by a matching ``session_items_added`` broadcast.
         *
         * The drop happens when the user is on a different session while
         * the canonical session's live items arrive: the WS handler skips
         * ``addSessionItems`` because ``itemsFetched`` is still false on
         * the canonical id, so ``_retireStreamingBlocks`` never runs. On
         * Codex specifically the retirement key is the wire-only
         * ``stream_uuid`` (not persisted), so by the time the user lands
         * on the session and items are fetched from the REST API, no
         * match is possible anymore and the synthetic ``streaming-block``
         * item would survive forever alongside the real ``agent_message``.
         *
         * Called from ``loadSessionData`` (SessionItemsList.vue) before
         * fetching items. Only ended blocks (``uuid !== null``) are
         * dropped, so active streaming visible when the user lands on a
         * session mid-turn keeps painting live deltas.
         */
        clearEndedStreamingBlocks(sessionId) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return

            const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
            const expanded = this.localState.sessionExpandedGroups[sessionId]
            const remaining = []
            let anyCleared = false
            for (const block of streaming.blocks) {
                if (block.uuid !== null) {
                    clearBlockInactivityTimer(block)
                    flushBuffer(sessionId, block.blockIndex)
                    // For thinking blocks: close the streaming detail key
                    // (otherwise a stale ``true`` for the synthetic lineNum
                    // would auto-open the next block landing at that slot)
                    // and drop the matching expandedGroups entry (no real
                    // item to migrate the expansion to — we never matched).
                    if (block.blockType === 'thinking') {
                        const streamingLineNum = baseLineNum - block.blockIndex
                        this.setDetailOpen(sessionId, `line:${streamingLineNum}:0`, false)
                        if (expanded && expanded.length > 0) {
                            const idx = expanded.indexOf(streamingLineNum)
                            if (idx !== -1) expanded.splice(idx, 1)
                        }
                    }
                    anyCleared = true
                } else {
                    remaining.push(block)
                }
            }
            if (!anyCleared) return

            if (remaining.length === 0) {
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            } else {
                streaming.blocks = remaining
            }
            this.recomputeVisualItems(sessionId)
        },

        // Session rename action

        /**
         * Rename a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {string} newTitle - The new title
         * @throws {Error} If the rename fails
         */
        async renameSession(projectId, sessionId, newTitle) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldTitle = session?.title

            if (session) {
                session.title = newTitle
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: newTitle })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to rename session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session && oldTitle !== undefined) {
                    session.title = oldTitle
                }
                throw error
            }
        },

        // --- MRU (Most Recently Used) navigation tracking ---

        /**
         * Record the current route in the MRU stack.
         * Replaces the previous entry for the same path, or for the same sessionId
         * (so each session only has one entry — the latest URL visited within it).
         * Entries without a sessionId (project pages) are deduplicated by path.
         * @param {string} path - The full route path (e.g. /project/abc/session/xyz/files)
         * @param {string|null} sessionId - The session ID from the route, or null
         */
        touchMruPath(path, sessionId) {
            const mru = this.localState.mruPaths
            // Remove previous entry for the same session (or same path if no session)
            const index = sessionId
                ? mru.findIndex(entry => entry.sessionId === sessionId)
                : mru.findIndex(entry => entry.path === path)
            if (index > -1) {
                mru.splice(index, 1)
            }
            mru.unshift({ path, sessionId })
            // Cap length to avoid unbounded growth
            if (mru.length > 100) {
                mru.length = 100
            }
        },

        /**
         * Remove all MRU entries for a given session.
         * Called when a session is archived or a draft is deleted.
         * @param {string} sessionId - The session ID to remove
         */
        removeMruSession(sessionId) {
            this.localState.mruPaths = this.localState.mruPaths.filter(
                entry => entry.sessionId !== sessionId
            )
        },

        /**
         * Find the next MRU path to navigate to.
         * Returns the path of the most recent entry whose session (if any)
         * is not archived and not a subagent.
         * @param {string|null} excludeSessionId - Session to exclude (typically the one being archived)
         * @returns {string|null} The path to navigate to, or null if none found
         */
        getNextMruPath(excludeSessionId = null) {
            for (const entry of this.localState.mruPaths) {
                if (entry.sessionId === excludeSessionId) continue
                // Entries without a session (project pages) are always valid
                if (!entry.sessionId) return entry.path
                // Entries with a session: check the session is still valid
                const session = this.sessions[entry.sessionId]
                if (!session) continue
                if (session.archived) continue
                if (session.parent_session_id) continue
                return entry.path
            }
            return null
        },

        /**
         * Set the archived state of a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {boolean} archived - Whether to archive or unarchive
         * @throws {Error} If the update fails
         */
        async setSessionArchived(projectId, sessionId, archived) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldArchived = session?.archived

            // Auto-unpin on archive: if archiving a pinned session and setting is enabled
            const settingsStore = useSettingsStore()
            const shouldUnpin = archived && session?.pinned && settingsStore.isAutoUnpinOnArchive
            const oldPinned = session?.pinned

            if (session) {
                session.archived = archived
                if (shouldUnpin) {
                    session.pinned = null
                }
            }

            // Remove from MRU when archiving
            if (archived) {
                this.removeMruSession(sessionId)
            }

            // Build the PATCH payload
            const patchData = { archived }
            if (shouldUnpin) {
                patchData.pinned = null
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(patchData)
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session) {
                    if (oldArchived !== undefined) {
                        session.archived = oldArchived
                    }
                    if (shouldUnpin && oldPinned !== undefined) {
                        session.pinned = oldPinned
                    }
                }
                throw error
            }
        },

        /**
         * Apply a bulk-archive broadcast from the backend. Local-only:
         * marks sessions as archived in the store and removes them from MRU.
         * Does NOT call the backend (the backend already archived them).
         * Does NOT touch pinned sessions (the backend filtered them out).
         */
        applyBulkArchiveFromBroadcast(sessionIds) {
            for (const sid of sessionIds) {
                const session = this.sessions[sid]
                if (session) {
                    session.archived = true
                }
                this.removeMruSession(sid)
            }
        },

        /**
         * Call the bulk-archive endpoint.
         *
         * @param {Object} params
         * @param {string} params.olderThan    - ISO timestamp threshold.
         * @param {Object} params.scope        - { type: 'project'|'workspace'|'all', id: string|null }.
         * @param {string} [params.titleQuery] - If non-empty, restrict to sessions whose title (or id)
         *                                       subsequence-matches the query — same semantics as the
         *                                       sidebar filter.
         * @param {boolean} [params.includeArchivedProjects] - For workspace/all scopes, include
         *                                       sessions belonging to archived projects. Ignored
         *                                       server-side for scope='project'.
         * @param {boolean} [params.dryRun]    - If true, returns only the count.
         * @param {AbortSignal} [params.signal] - Abort signal for cancellable dry-runs.
         * @returns {Promise<{count: number, has_archived_in_scope: boolean}>}
         */
        async bulkArchiveSessions({
            olderThan,
            scope,
            titleQuery = '',
            includeArchivedProjects = false,
            dryRun = false,
            signal = null,
        }) {
            const body = {
                older_than: olderThan,
                scope: scope.type,
                dry_run: dryRun,
            }
            if (scope.type === 'project') body.project_id = scope.id
            if (scope.type === 'workspace') body.workspace_id = scope.id
            if (titleQuery) body.title_query = titleQuery
            if (includeArchivedProjects) body.include_archived_projects = true

            const res = await apiFetch('/api/sessions/bulk-archive/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal,
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.error || `HTTP ${res.status}`)
            }
            return res.json()
        },

        /**
         * Set the pin mode of a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {('project'|'workspace'|'all'|null)} mode - Pin mode, or null to unpin
         * @throws {Error} If the update fails
         */
        async setSessionPinMode(projectId, sessionId, mode) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldPinned = session?.pinned

            if (session) {
                session.pinned = mode
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ pinned: mode })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session && oldPinned !== undefined) {
                    session.pinned = oldPinned
                }
                throw error
            }
        },

        // Draft messages actions

        /**
         * Get or create a debounced save function for a session.
         * @param {string} sessionId
         * @returns {Function} Debounced save function
         * @private
         */
        _getDebouncedSave(sessionId) {
            if (!debouncedSaves.has(sessionId)) {
                debouncedSaves.set(sessionId, debounce((draft) => {
                    saveDraftMessage(sessionId, draft).catch(err =>
                        console.warn('Failed to save draft message to IndexedDB:', err)
                    )
                }, 500))
            }
            return debouncedSaves.get(sessionId)
        },

        /**
         * Set the draft message for a session.
         * Called by MessageInput on each keystroke.
         * If message is empty, clears the draft entirely.
         * @param {string} sessionId
         * @param {string} message
         */
        setDraftMessage(sessionId, message) {
            if (!message) {
                // Message is empty - clear the draft
                if (this.localState.draftMessages[sessionId]) {
                    this.clearDraftMessage(sessionId)
                }
                return
            }

            // Message has content - save it
            this.localState.draftMessages[sessionId] = { message }

            // Persist to IndexedDB with debounce
            const debouncedSave = this._getDebouncedSave(sessionId)
            debouncedSave({ message })
        },

        /**
         * Set the draft title for a session (draft sessions only).
         * Called by SessionRenameDialog when title is modified before first message.
         * Updates the draft session in IndexedDB with the new title.
         * @param {string} sessionId
         * @param {string} title
         */
        setDraftTitle(sessionId, title) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return

            // Update IndexedDB with projectId, title, and provider (fire and forget)
            saveDraftSession(sessionId, {
                projectId: session.project_id,
                title,
                provider: session.provider,
            }).catch(err =>
                console.warn('Failed to save draft session title to IndexedDB:', err)
            )
        },

        /**
         * Clear the draft for a session.
         * Called after successful message send.
         * @param {string} sessionId
         */
        clearDraftMessage(sessionId) {
            delete this.localState.draftMessages[sessionId]

            // Cancel any pending debounced save
            const debouncedSave = debouncedSaves.get(sessionId)
            if (debouncedSave) {
                debouncedSave.cancel()
                debouncedSaves.delete(sessionId)
            }

            // Delete from IndexedDB
            deleteDraftMessage(sessionId).catch(err =>
                console.warn('Failed to delete draft message from IndexedDB:', err)
            )
        },

        /**
         * Load all draft messages from IndexedDB into local state.
         * Called at app startup.
         */
        async hydrateDraftMessages() {
            try {
                const drafts = await getAllDraftMessages()
                this.localState.draftMessages = drafts
            } catch (err) {
                console.warn('Failed to load draft messages from IndexedDB:', err)
            }
        },

        /**
         * Load all draft sessions from IndexedDB into the sessions store.
         * Called at app startup, BEFORE hydrateDraftMessages.
         * Recreates session objects with: id, project_id, title (or 'New session'),
         * mtime=now, last_line=0, draft=true.
         */
        async hydrateDraftSessions() {
            try {
                const draftSessions = await getAllDraftSessions()
                const now = Date.now() / 1000
                const defaultProvider = useSettingsStore().defaultProvider
                for (const [sessionId, { projectId, title, provider }] of Object.entries(draftSessions)) {
                    this.sessions[sessionId] = {
                        id: sessionId,
                        project_id: projectId,
                        provider: provider || defaultProvider,
                        title: title || null,  // null = user hasn't set a title yet
                        mtime: now,
                        last_line: 0,
                        draft: true,
                    }
                }
            } catch (err) {
                console.warn('Failed to load draft sessions from IndexedDB:', err)
            }
        },

        // Draft session cleanup

        /**
         * Clean up orphan draft sessions from IndexedDB.
         * Reads all draft sessions from IndexedDB and checks against the backend API.
         * If a session exists on the backend, the draft entry is removed from IndexedDB
         * (and from the store if it still has draft: true).
         * Errors are silently ignored — the next cycle will retry.
         */
        async cleanupOrphanDraftSessions() {
            let draftSessions
            try {
                draftSessions = await getAllDraftSessions()
            } catch {
                return  // IndexedDB error, retry next cycle
            }

            const entries = Object.entries(draftSessions)
            if (entries.length === 0) return

            for (const [sessionId, data] of entries) {
                const projectId = data?.projectId
                if (!projectId) {
                    // Corrupted entry — no project ID means we can't check the API, just remove it
                    deleteDraftSessionFromDb(sessionId).catch(() => {})
                    if (this.sessions[sessionId]?.draft) {
                        delete this.sessions[sessionId]
                    }
                    continue
                }
                try {
                    const response = await apiFetch(
                        `/api/projects/${projectId}/sessions/${sessionId}/`,
                        { method: 'HEAD' }
                    )
                    if (response.ok) {
                        // Session exists on backend — remove the orphan draft
                        deleteDraftSessionFromDb(sessionId).catch(() => {})
                        if (this.sessions[sessionId]?.draft) {
                            delete this.sessions[sessionId]
                        }
                    }
                    // 404 = genuine draft, keep it. Other errors = skip silently.
                } catch {
                    // Network error, skip this session
                }
            }
        },

        // Title suggestion actions

        /**
         * Handle title_suggested message from WebSocket.
         * Always stores sourcePrompt (for regeneration), and suggestion if available.
         * @param {Object} data - { sessionId, suggestion, sourcePrompt }
         */
        handleTitleSuggested(data) {
            const { sessionId, suggestion, sourcePrompt } = data
            // Resolve the draft alias if the backend echoed the draft id back
            // (the ``suggest_title`` payload was sent under the draft id; for
            // providers that rebind to a canonical id, we want the response
            // to land on the canonical key so the SessionView watcher sees it).
            const sid = this.localState.draftAliases[sessionId] || sessionId
            // Always store the response so the frontend knows the request completed
            // (distinguishes "no response yet" from "response received with failure")
            this.localState.titleSuggestions[sid] = {
                suggestion: suggestion || null,
                sourcePrompt: sourcePrompt || null,
            }
        },

        /**
         * Clear title suggestion for a session (after use).
         * @param {string} sessionId
         */
        clearTitleSuggestion(sessionId) {
            delete this.localState.titleSuggestions[sessionId]
        },

        /**
         * Register a session as waiting on an auto-applied title.
         * Consumed by the App-level watcher which reacts to the matching
         * ``titleSuggestions`` entry.
         * @param {string} sessionId
         * @param {string} projectId
         */
        registerPendingTitleAutoApply(sessionId, projectId) {
            this.localState.pendingTitleAutoApply[sessionId] = { projectId }
        },

        /**
         * Drop a pending auto-apply entry (after success or definitive failure).
         * @param {string} sessionId
         */
        clearPendingTitleAutoApply(sessionId) {
            delete this.localState.pendingTitleAutoApply[sessionId]
        },

        // =========================================================================
        // Attachment actions (for document upload)
        // =========================================================================

        /**
         * Add a file attachment to a session.
         *
         * Processes the file (validation + encoding) using the provider's
         * attachment capabilities and stores in IndexedDB. The provider is
         * resolved from the session row so call sites don't have to thread
         * the capabilities through themselves — a stray drop on a Codex
         * session validates against Codex rules without the caller knowing.
         *
         * @param {string} sessionId - The session ID
         * @param {File} file - The file to add
         * @returns {Promise<DraftMedia>} The processed media object
         * @throws {Error} If validation fails or file cannot be processed
         */
        async addAttachment(sessionId, file) {
            const session = this.getSession(sessionId)
            const helpers = getProviderHelpers(session?.provider)
            const capabilities = helpers?.getAttachmentSupport() ?? {
                images: false, documents: false, maxBytes: 0,
                acceptedMimeTypes: [], resizeImages: false,
            }

            // Per-draft hard caps (uniform across providers): refuse the
            // upload up front rather than running the (potentially slow)
            // resize pipeline only to discover the draft can't take more.
            const existing = this.localState.attachments[sessionId]
            const existingCount = existing?.size ?? 0
            if (existingCount >= MAX_FILES_PER_DRAFT) {
                throw new Error(
                    `Maximum ${MAX_FILES_PER_DRAFT} files per draft reached`,
                )
            }
            let storedBytes = 0
            if (existing) {
                for (const media of existing.values()) {
                    storedBytes += getDraftMediaBytes(media)
                }
            }
            // Conservative check: compare stored (post-resize) total to
            // 32 MB minus the new file's raw source size. The new file
            // will shrink after resize, but blocking on the raw size is
            // safer and avoids running the encode pipeline for an upload
            // we'd reject anyway.
            if (storedBytes + file.size > MAX_TOTAL_BYTES_PER_DRAFT) {
                const totalMB = (MAX_TOTAL_BYTES_PER_DRAFT / 1024 / 1024).toFixed(0)
                const storedMB = (storedBytes / 1024 / 1024).toFixed(1)
                throw new Error(
                    `Draft total size limit reached (${totalMB} MB max; ${storedMB} MB already attached)`,
                )
            }

            // Track that a file is being processed (blocks the send button)
            this.localState.processingAttachments[sessionId] =
                (this.localState.processingAttachments[sessionId] || 0) + 1

            try {
                // Process file (validates and encodes)
                const media = await processFile(file, sessionId, capabilities)

                // Save to IndexedDB
                await saveDraftMedia(media)

                // Update in-memory state
                if (!this.localState.attachments[sessionId]) {
                    this.localState.attachments[sessionId] = new Map()
                }
                this.localState.attachments[sessionId].set(media.id, media)

                // Update draft message with media ID (for order preservation)
                const draft = await getDraftMessage(sessionId) || {}
                draft.mediaIds = draft.mediaIds || []
                draft.mediaIds.push(media.id)
                await saveDraftMessage(sessionId, draft)

                return media
            } finally {
                // Decrement counter (whether success or failure)
                this.localState.processingAttachments[sessionId]--
                if (this.localState.processingAttachments[sessionId] <= 0) {
                    delete this.localState.processingAttachments[sessionId]
                }
            }
        },

        /**
         * Remove an attachment from a session.
         * @param {string} sessionId - The session ID
         * @param {string} mediaId - The media ID to remove
         */
        async removeAttachment(sessionId, mediaId) {
            // Remove from IndexedDB
            await deleteDraftMedia(mediaId)

            // Remove from in-memory state
            this.localState.attachments[sessionId]?.delete(mediaId)

            // Update draft message to remove media ID
            const draft = await getDraftMessage(sessionId)
            if (draft?.mediaIds) {
                draft.mediaIds = draft.mediaIds.filter(id => id !== mediaId)
                await saveDraftMessage(sessionId, draft)
            }
        },

        /**
         * Remove every non-image attachment (PDF, TXT) from a draft.
         *
         * Used by the provider-switcher UX in the agent settings popover:
         * Codex has no protocol for documents, so when a draft holds any
         * PDF/TXT the Codex option is gated behind an explicit "remove
         * the documents to continue" affordance. Returns the count of
         * removed attachments so the caller can toast a confirmation.
         *
         * @param {string} sessionId - The session ID
         * @returns {Promise<number>} Number of attachments removed
         */
        async removeNonImageAttachments(sessionId) {
            const map = this.localState.attachments[sessionId]
            if (!map || map.size === 0) return 0
            const toRemove = []
            for (const media of map.values()) {
                if (media.type !== 'image') toRemove.push(media.id)
            }
            for (const id of toRemove) {
                await this.removeAttachment(sessionId, id)
            }
            return toRemove.length
        },

        /**
         * Load attachments for a session from IndexedDB.
         * Called when entering a session to restore persisted attachments.
         * @param {string} sessionId - The session ID
         */
        async loadAttachmentsForSession(sessionId) {
            try {
                const medias = await getDraftMediasBySession(sessionId)
                if (medias.length > 0) {
                    this.localState.attachments[sessionId] = new Map(
                        medias.map(m => [m.id, m])
                    )
                }
            } catch (err) {
                console.warn('Failed to load attachments from IndexedDB:', err)
            }
        },

        /**
         * Clear all attachments for a session.
         * Called after successful message send.
         * @param {string} sessionId - The session ID
         */
        async clearAttachmentsForSession(sessionId) {
            // Remove from IndexedDB
            await deleteAllDraftMediasForSession(sessionId)

            // Clear in-memory state
            delete this.localState.attachments[sessionId]
        },

        /**
         * Get attachments in Claude SDK format (images and documents separated).
         * @param {string} sessionId - The session ID
         * @returns {{ images: Object[], documents: Object[] }} SDK-formatted blocks
         */
        getAttachmentsForSdk(sessionId) {
            const map = this.localState.attachments[sessionId]
            if (!map || map.size === 0) {
                return { images: [], documents: [] }
            }
            return mediasToSdkFormat(Array.from(map.values()))
        },

        /**
         * Load all draft attachments from IndexedDB into local state.
         * Called at app startup.
         */
        async hydrateAttachments() {
            try {
                const allMedias = await getAllDraftMedias()
                // Group by sessionId
                for (const media of allMedias) {
                    if (!this.localState.attachments[media.sessionId]) {
                        this.localState.attachments[media.sessionId] = new Map()
                    }
                    this.localState.attachments[media.sessionId].set(media.id, media)
                }
            } catch (err) {
                console.warn('Failed to load attachments from IndexedDB:', err)
            }
        }
    }
})

// Pinia HMR support: hot-replace actions/getters without full page reload.
// We wrap acceptHMRUpdate with state save/restore because Pinia's patchObject
// loses dynamic keys: it skips keys present in the old state but absent from
// the fresh state() initializer (e.g. projects: {} starts empty, so all
// runtime-added project IDs are dropped during the merge).
if (import.meta.hot) {
    // Create the HMR handler once at module eval time (standard Pinia pattern).
    // We wrap it to save/restore state around the call because Pinia's patchObject
    // loses dynamic keys (it skips old keys absent from the fresh state() initializer).
    const piniaHmrHandler = acceptHMRUpdate(useDataStore, import.meta.hot)

    import.meta.hot.accept((newModule) => {
        const pinia = import.meta.hot.data?.pinia || useDataStore._pinia
        if (!pinia) return
        const store = pinia._s.get('data')
        if (!store) return

        // Save current state values (raw references, no cloning needed)
        const savedState = {}
        for (const key of Object.keys(store.$state)) {
            savedState[key] = toRaw(store.$state[key])
        }

        // Apply Pinia's HMR update (updates actions/getters but loses dynamic state keys)
        piniaHmrHandler(newModule)

        // Restore state values that were lost by patchObject
        store.$patch((state) => {
            for (const [key, value] of Object.entries(savedState)) {
                state[key] = value
            }
        })
    })
}
