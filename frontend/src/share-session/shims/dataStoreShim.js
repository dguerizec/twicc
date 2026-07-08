// Read-only mirror of the SPA data store for the share bundle. Only the surface
// the reused transcript components actually touch is implemented; anything else
// throws in dev so drift is caught, and no-ops the write surface (failed sends,
// api-error recovery) that read-only mode never exercises.
import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { computeVisualItems, insertDaySeparators, visualItemEqual } from '../../utils/visualItems'
import { getParsedContent, setParsedContent, clearParsedContent } from '../../utils/parsedContent'
import { shareApi } from './shareApi'
import { useSettingsStore } from '../../stores/settings' // aliased to settingsStoreShim

function rangesToQS(ranges) {
    const params = new URLSearchParams()
    for (const range of ranges) {
        if (typeof range === 'number' || typeof range === 'string') {
            params.append('range', String(range))
        } else if (Array.isArray(range)) {
            const [lo, hi] = range
            params.append('range', `${lo ?? ''}:${hi ?? ''}`)
        }
    }
    return params.toString()
}

export const useDataStore = defineStore('shareData', {
    state: () => ({
        // The single shared session (plus subagents keyed by id).
        sessions: {},            // id -> session-ish meta
        sessionItems: {},        // id -> [{ line_num, content, display_level, group_head, group_tail, kind, timestamp }]
        visualItems: {},         // id -> stabilized visual items
        expandedGroups: {},      // id -> [groupHeadLineNum]
        internalExpandedGroups: {},
        detailedBlocks: {},      // id -> [userMessageLineNum]
        openDetails: {},         // id -> { key: bool }
        agentLinks: {},          // id -> { toolId: { agentId, isBackground, toolUseLineNum, slug } }
        toolStates: {},          // id -> { toolId: {...} }
        _cache: {},              // id -> Map for visual-item stabilization
    }),
    getters: {
        getSession: (s) => (id) => s.sessions[id] || null,
        getProject: () => () => null,
        getSessionItems: (s) => (id) => s.sessionItems[id] || [],
        getSessionItem: (s) => (id, lineNum) => {
            const items = s.sessionItems[id]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },
        getSessionVisualItems: (s) => (id) => s.visualItems[id] || [],
        getExpandedGroups: (s) => (id) => s.expandedGroups[id] || [],
        getInternalExpandedGroups: (s) => (id, lineNum) => (s.internalExpandedGroups[id]?.[lineNum]) || [],
        isBlockDetailed: (s) => (id, u) => (s.detailedBlocks[id] || []).includes(u),
        isDetailOpen: (s) => (id, key) => !!s.openDetails[id]?.[key],
        getAgentLink: (s) => (id, toolId) => s.agentLinks[id]?.[toolId],
        getAgentToolUseLineNum: (s) => (parentId, subId) => {
            const links = s.agentLinks[parentId]
            if (!links) return null
            for (const l of Object.values(links)) if (l.agentId === subId) return l.toolUseLineNum ?? null
            return null
        },
        getWorkflowLink: () => () => undefined,          // no "View Workflow" in shares
        getToolState: (s) => (id, toolId) => s.toolStates[id]?.[toolId] || null,
        getProcessState: () => () => null,               // never live → no spinners/stop
        getPendingRequests: () => () => [],
        isItemLive: () => () => false,
        isStartupInProgress: () => () => false,
        getProjectIndicatorScopeIds: () => () => [],
        // failed-send / api-error read surface (unused in read-only)
        getFailedSend: () => () => null,
        apiErrorRecovery: () => () => null,
    },
    actions: {
        // ── Loading ──────────────────────────────────────────────────────
        async loadSessionMetadata(_projectId, sessionId, parentSessionId = null) {
            try { return await shareApi().fetchItemsMetadata(parentSessionId ? sessionId : null) }
            catch { return null }
        },
        initSessionItemsFromMetadata(sessionId, metadata) {
            this.sessionItems[sessionId] = metadata.map((m) => ({
                line_num: m.line_num, display_level: m.display_level,
                group_head: m.group_head, group_tail: m.group_tail,
                kind: m.kind, timestamp: m.timestamp ?? null, content: null,
            }))
            this.recomputeVisualItems(sessionId)
        },
        updateSessionItemsContent(sessionId, items) {
            const arr = this.sessionItems[sessionId]
            if (!arr) return
            for (const it of items) {
                const i = it.line_num - 1
                if (!arr[i]) continue
                arr[i].content = it.content
                clearParsedContent(arr[i])
                if (it.display_level != null) arr[i].display_level = it.display_level
                if (it.group_head != null) arr[i].group_head = it.group_head
                if (it.group_tail != null) arr[i].group_tail = it.group_tail
                if (it.kind !== undefined) arr[i].kind = it.kind
                if (it.timestamp !== undefined) arr[i].timestamp = it.timestamp
            }
            this.recomputeVisualItems(sessionId)
        },
        addSessionItems(sessionId, items) {
            const arr = this.sessionItems[sessionId] || (this.sessionItems[sessionId] = [])
            for (const it of items) {
                const i = it.line_num - 1
                arr[i] = { ...it, content: it.content ?? null }
                clearParsedContent(arr[i])
            }
            this.recomputeVisualItems(sessionId)
        },
        async loadSessionItemsRanges(_projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return true
            const qs = rangesToQS(ranges)
            if (!qs) return false
            try {
                const items = await shareApi().fetchItems(qs, parentSessionId ? sessionId : null)
                this.addSessionItems(sessionId, items)
                return true
            } catch { return false }
        },
        areSessionItemsFetched(sessionId) { return !!this.sessionItems[sessionId] },

        // ── Visual items (simplified: no streaming/optimistic/working/failed) ──
        recomputeVisualItems(sessionId) {
            const items = this.sessionItems[sessionId] || []
            if (!items.length) { this.visualItems[sessionId] = []; this._cache[sessionId] = new Map(); return }
            const settings = useSettingsStore()
            const mode = settings.getDisplayMode
            const expanded = this.expandedGroups[sessionId] || []
            const detailed = new Set(this.detailedBlocks[sessionId] || [])
            const vis = computeVisualItems(items, mode, expanded, false, detailed)
            for (let i = 0; i < vis.length; i++) {
                const isUser = vis[i].kind === 'user_message'
                const prevUser = i > 0 ? vis[i - 1].kind === 'user_message' : null
                const nextUser = i < vis.length - 1 ? vis[i + 1].kind === 'user_message' : null
                vis[i].isBlockStart = i === 0 || isUser !== prevUser
                vis[i].isBlockEnd = i === vis.length - 1 || isUser !== nextUser
            }
            const render = settings.areMessageTimestampsShown ? insertDaySeparators(vis) : vis
            const cache = this._cache[sessionId] || new Map()
            const next = new Map()
            const stable = render.map((vi) => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    const p = getParsedContent(vi); if (p !== null) setParsedContent(cached, p)
                    next.set(vi.lineNum, cached); return cached
                }
                const p = getParsedContent(vi); if (p !== null) setParsedContent(vi, p)
                next.set(vi.lineNum, vi); return vi
            })
            this._cache[sessionId] = next
            this.visualItems[sessionId] = stable
        },

        // ── Toggles / detail state (persist within the tab session) ──────
        toggleExpandedGroup(sessionId, head) {
            const arr = this.expandedGroups[sessionId] || (this.expandedGroups[sessionId] = [])
            const i = arr.indexOf(head)
            if (i >= 0) arr.splice(i, 1); else arr.push(head)
            this.recomputeVisualItems(sessionId)
        },
        toggleInternalExpandedGroup(sessionId, lineNum, startIndex) {
            const s = this.internalExpandedGroups[sessionId] || (this.internalExpandedGroups[sessionId] = {})
            const arr = s[lineNum] || (s[lineNum] = [])
            const i = arr.indexOf(startIndex)
            if (i >= 0) arr.splice(i, 1); else arr.push(startIndex)
        },
        toggleBlockDetailedMode(sessionId, u) {
            const arr = this.detailedBlocks[sessionId] || (this.detailedBlocks[sessionId] = [])
            const i = arr.indexOf(u)
            if (i >= 0) arr.splice(i, 1); else arr.push(u)
            this.recomputeVisualItems(sessionId)
        },
        setDetailOpen(sessionId, key, open) {
            const s = this.openDetails[sessionId] || (this.openDetails[sessionId] = {})
            if (open) s[key] = true; else delete s[key]
        },

        // ── Seed helpers used by ShareSessionApp ─────────────────────────
        setSession(session) { this.sessions[session.id] = markRaw(session) },
        setAgentLinks(sessionId, links) {
            const map = {}
            for (const l of links) map[l.tool_use_id] = {
                agentId: l.agent_id, isBackground: l.is_background,
                toolUseLineNum: l.tool_use_line_num, slug: l.agent_slug || null,
            }
            this.agentLinks[sessionId] = map
        },

        // ── No-op write surface (statically imported by reused components) ──
        registerOutgoingSend() {}, removeFailedSend() {}, restoreDraftAttachments() {},
        setProcessState() {}, markItemsLive() {}, clearEndedStreamingBlocks() {},
        auditInflightSends() {}, ensureSessionItemsCoverage() { return Promise.resolve() },
        fetchToolStates() { return Promise.resolve() },
    },
})
