<script setup>
/**
 * PeerMessageReviewDialog — read a pending peer message and route it.
 *
 * The receiving-side human gate (design §6): the full message (markdown +
 * attachments) is read here, then delivered to an existing session, to a new
 * session in a picked project, or refused. The message itself is never
 * editable — an optional recipient note is injected alongside instead.
 *
 * Also the read-back surface for a resolved message (reopened from the inbox
 * history): an already-delivered one offers the delivery pickers again (wrong
 * target picked, draft cleared), a refused or outbound one is read-only.
 *
 * The full payload (base64 blobs) is fetched from REST on open; the store
 * only ever holds summaries.
 */
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePeersStore } from '../../stores/peers'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { SESSION_TIME_FORMAT } from '../../constants'
import { formatDate } from '../../utils/date'
import { apiFetch } from '../../utils/api'
import { renderMarkdown } from '../../utils/markdown'
import { sdkBlockToMediaItem } from '../../utils/fileUtils'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { computeSidebarSessionBlocks } from '../../utils/sidebarSessions'
import { dateBucketSeparator } from '../../utils/datePresets'
import { matchQuery } from '../../utils/textFilter'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import { ensureProjectTrust } from '../../composables/useTrustGate'
import { useProjectMark } from '../../composables/useProjectMark'
import MediaThumbnailGroup from '../media/MediaThumbnailGroup.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import ProjectMark from '../project/ProjectMark.vue'
import ProjectSelectOptions from '../project/ProjectSelectOptions.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import SidebarListSeparator from '../sidebar/SidebarListSeparator.vue'

const props = defineProps({
    open: Boolean,
    messageId: { type: [Number, String], default: null },
})
// `close` carries an optional reason: 'navigating' when the dialog closes
// because the user is being sent to the delivery target (see prefillComposer).
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const router = useRouter()
const route = useRoute()

// The provenance timestamp follows the global time-format setting, like the
// inbox rows and every other timestamp in the app.
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

const dialogRef = ref(null)
const detail = ref(null)          // full serialize_peer_message (with payload)
const loadError = ref('')
const renderedText = ref('')      // renderMarkdown is async — never bind the promise
const note = ref('')
const actionError = ref('')
const busy = ref(false)
const confirmingRefuse = ref(false)

// Delivery pickers.
const mode = ref(null)            // null | 'existing' | 'new'
const pickedProjectId = ref('')   // 'new' mode: wa-select value
const sessionFilter = ref('')
// 'existing' mode: the scope the session list is built from — the sidebar
// frame by default, narrowable to one project (or the frame's workspace).
const scopeId = ref(ALL_PROJECTS_ID)
// 'existing' mode: a click only SELECTS (highlight); the explicit Deliver
// button sends — no accidental one-click delivery.
const selectedSessionId = ref(null)

const peerName = computed(() => peersStore.peerLabel(detail.value?.peer_id))
const origin = computed(() => detail.value?.origin || {})
const isInbound = computed(() => detail.value?.direction === 'in')
const isPending = computed(() => isInbound.value && detail.value?.status === 'pending')
// Redelivery (design decision, 2026-08-10): the owner routed the message into
// the wrong session, or cleared the prefilled draft. A delivered message stays
// re-routable — the peer already got its "delivered" answer, so nothing changes
// for them. A REFUSED one never reopens: that answer stands.
const isRedeliverable = computed(() => isInbound.value && detail.value?.status === 'delivered')
const canDeliver = computed(() => isPending.value || isRedeliverable.value)
// Attachment bytes are dropped 7 days after resolution — a late redelivery
// carries the text only.
const attachmentsLost = computed(() =>
    isRedeliverable.value && detail.value?.purged && (detail.value?.attachments_meta?.length || 0) > 0
)
// Header and routing read exactly like an inbox row (PeerInboxRow.vue): same
// arrow, same colours, same labelled "<verb> session “…” in <project>" line.
// The two surfaces show the same facts; they must not describe them twice, in
// two different vocabularies.
const headIcon = computed(() => {
    if (isInbound.value && isPending.value) return 'envelope'
    return isInbound.value ? 'arrow-down' : 'arrow-up'
})
const directionLabel = computed(() =>
    isInbound.value ? `Received from ${peerName.value}` : `Sent to ${peerName.value}`
)
const statusVariant = computed(() => {
    if (detail.value?.status === 'delivered') return 'success'
    if (detail.value?.status === 'pending') return 'neutral'
    return 'danger'
})
const sentAt = computed(() => {
    const iso = origin.value.sent_at
    return iso ? new Date(iso) : null
})
/** The local end of the exchange: where an inbound message landed, where an
 *  outbound one left from. Nothing of the peer's own context is shown — it
 *  never crosses the wire. */
const localRoute = computed(() => {
    // Title and project ride with the message, read live from the session row
    // server-side — never resolved against the front's store, and never an id.
    // A session that no longer exists (FK nulled) shows no line at all.
    const local = isInbound.value ? detail.value?.delivered_to_session : detail.value?.origin_session
    if (!local) return null
    return {
        label: isInbound.value ? 'Delivered to session' : 'Sent from session',
        title: local.title || 'Untitled session',
        projectId: local.project_id || null,
        sessionId: local.id,
    }
})

/** Open the session this message belongs to, exactly like clicking it in the
 *  sidebar: `sessionRouteLocation` keeps the current frame — the project
 *  filter and the active workspace — and changes only the session. */
function openLocalSession() {
    if (!localRoute.value?.sessionId) return
    const target = { id: localRoute.value.sessionId, project_id: localRoute.value.projectId }
    // 'navigating': the user leaves for the session, so the inbox must not
    // reopen on top of it.
    emit('close', 'navigating')
    router.push(sessionRouteLocation(target, route))
}

const mediaItems = computed(() => {
    const payload = detail.value?.payload
    if (!payload) return []
    return [...(payload.images || []), ...(payload.documents || [])]
        .map(sdkBlockToMediaItem)
        .filter(Boolean)
})

const workspacesStore = useWorkspacesStore()

// Project pickers (both modes): the same wa-select + ProjectSelectOptions the
// other new-session flows use (badges, named/tree split, workspace priority),
// non-stale and non-archived. `include-worktrees` lists each repository's
// worktrees under it, like the sidebar's "New session" picker — a worktree is
// a delivery target of its own.
const selectableProjects = computed(() =>
    dataStore.getListableProjects.filter(p => !p.archived && !p.stale)
)

/** Is `projectId` offered by the scope select — a listed repository, or one of
 *  their listed worktrees? */
function isSelectableProject(projectId) {
    const project = dataStore.getProject(projectId)
    if (!project || project.archived || project.stale) return false
    if (!project.worktree_of) return selectableProjects.value.some(p => p.id === projectId)
    return selectableProjects.value.some(p => p.id === project.worktree_of)
}

// Current sidebar frame (the dialog is global — derive it from the route,
// exactly like SessionList does).
const effectiveProjectId = computed(() => route.params.projectId || ALL_PROJECTS_ID)
const activeWorkspaceId = computed(() => {
    if (isWorkspaceProjectId(effectiveProjectId.value)) return extractWorkspaceId(effectiveProjectId.value)
    return route.query.workspace || null
})
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)

/** The scope the session picker opens on: the sidebar frame — the project (or
 *  workspace) the user is already looking at is where a message most often
 *  goes. Anything the select does not offer falls back to all projects. */
function defaultScopeId() {
    const frameId = effectiveProjectId.value
    if (isWorkspaceProjectId(frameId)) return activeWorkspace.value ? frameId : ALL_PROJECTS_ID
    return isSelectableProject(frameId) ? frameId : ALL_PROJECTS_ID
}

// Icon + dot of the picked scope, for the select's own button (a wa-select
// shows the option's label as plain text, never its rendered content).
const { iconUrl: scopeIconUrl, dotColor: scopeDotColor } = useProjectMark(scopeId)

// 'Existing session' picker: the sidebar's session list — same order and
// blocks (pinned/active/natural + date buckets) — minus archived (never a
// delivery target here) and drafts (no real session to inject into).
//
// The sidebar's cross-filter blocks ("Pinned elsewhere", "Active elsewhere")
// are deliberately dropped: they exist to keep out-of-scope sessions reachable
// while browsing, which is exactly what a scope select must not do. Sessions
// of another project are reached by picking that project.
const sessionRows = computed(() => {
    if (mode.value !== 'existing') return []
    const blocks = computeSidebarSessionBlocks({
        data: dataStore,
        workspaces: workspacesStore,
        effectiveProjectId: scopeId.value,
        activeWorkspaceId: activeWorkspaceId.value,
        sessionId: null,
        showArchived: false,
        showArchivedProjects: false,
        showActiveAcrossFilters: false,
    })
    const processStates = dataStore.processStates
    const nowMs = Date.now()
    const entries = []
    const push = (session, sectionKey, separator) => {
        if (session.draft || session.archived) return
        entries.push({ session, sectionKey, separator })
    }
    for (const s of blocks.natural) {
        if (s.pinned) push(s, 'n-pinned', { label: 'Pinned' })
        else if (processStates[s.id] != null) push(s, 'n-active', { label: 'Active' })
        else {
            const bucket = dateBucketSeparator(s.mtime, nowMs)
            push(s, `n-${bucket.key}`, bucket.entry)
        }
    }
    // Same matching as the sidebar's filter: fuzzy by default, exact
    // substring when the query is wrapped/prefixed with a quote.
    const query = sessionFilter.value.trim()
    const visible = query
        ? entries.filter(e => matchQuery(query, e.session.title || e.session.id))
        : entries
    // A separator lands on the first VISIBLE session of each section.
    let prevSection = null
    return visible.map((entry) => {
        const withSeparator = entry.sectionKey !== prevSection
        prevSection = entry.sectionKey
        return { ...entry, separator: withSeparator ? entry.separator : null }
    })
})

const selectedSession = computed(() =>
    sessionRows.value.find(r => r.session.id === selectedSessionId.value)?.session || null
)

watch(() => [props.open, props.messageId], async ([open]) => {
    if (!open || props.messageId == null) return
    detail.value = null
    loadError.value = ''
    renderedText.value = ''
    note.value = ''
    actionError.value = ''
    mode.value = null
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
    confirmingRefuse.value = false
    try {
        const response = await apiFetch(`/api/peer-messages/${props.messageId}/`)
        if (!response.ok) {
            loadError.value = 'Could not load the message.'
            return
        }
        detail.value = await response.json()
        // Redelivery: bring back the note typed the first time (empty on a
        // never-delivered message).
        note.value = detail.value?.recipient_note || ''
    } catch {
        // fetch rejects on network failure — never leave the dialog blank.
        loadError.value = 'Could not load the message — is the server reachable?'
        return
    }
    renderedText.value = await renderMarkdown(detail.value?.payload?.text || '')
}, { immediate: true })

/** Toggle a delivery mode; every picker starts fresh on each switch. */
function setMode(next) {
    mode.value = mode.value === next ? null : next
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
}

function errorText(payload) {
    const errors = payload?.errors
    if (Array.isArray(errors) && errors.length) {
        return errors.map(e => e.message || e.code).join(' — ')
    }
    return payload?.error || 'Request failed.'
}

/** Rebuild a File from an SDK attachment block so the normal draft-attachment
 *  pipeline (validation, resize, IndexedDB) processes it like a user upload. */
function blockToFile(block, index) {
    const source = block?.source || {}
    if (source.type === 'text' && typeof source.data === 'string') {
        return new File([source.data], `peer-attachment-${index + 1}.txt`, { type: 'text/plain' })
    }
    if (source.type === 'base64' && typeof source.data === 'string') {
        const mime = source.media_type || 'application/octet-stream'
        const bytes = Uint8Array.from(atob(source.data), c => c.charCodeAt(0))
        const ext = mime === 'application/pdf' ? 'pdf' : (mime.split('/')[1] || 'bin')
        return new File([bytes], block.title || `peer-attachment-${index + 1}.${ext}`, { type: mime })
    }
    return null
}

/** Ask the backend to resolve the message as delivered; returns the envelope
 *  text to prefill a composer with, or null on failure (actionError set). */
async function markDelivered(sessionId) {
    const response = await apiFetch(`/api/peer-messages/${props.messageId}/deliver/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: sessionId || undefined,
            note: note.value,
            // Opt-in server-side: an already-delivered message is only
            // re-routed when the UI asks for it explicitly.
            redeliver: isRedeliverable.value || undefined,
        }),
    })
    let payload = null
    try { payload = await response.json() } catch { /* empty */ }
    if (!response.ok) {
        actionError.value = errorText(payload)
        return null
    }
    return payload.envelope
}

/** Prefill a composer (existing session's draft, or a fresh draft session)
 *  with the envelope + the peer attachments, then jump to it. Nothing is
 *  sent — the user reviews and sends through the normal pipeline. */
async function prefillComposer(sessionId, projectId) {
    // Append — the target session may already carry a user-typed draft,
    // which must never be overwritten.
    const existing = dataStore.getDraftMessage(sessionId)?.message?.trim() || ''
    dataStore.setDraftMessage(sessionId, existing ? `${existing}\n\n${envelopeText}` : envelopeText)
    const blocks = [
        ...(detail.value?.payload?.images || []),
        ...(detail.value?.payload?.documents || []),
    ]
    for (const [index, block] of blocks.entries()) {
        const file = blockToFile(block, index)
        if (!file) continue
        try {
            await dataStore.addAttachment(sessionId, file)
        } catch (err) {
            console.warn('[peer] could not re-attach a peer file to the draft:', err)
        }
    }
    // 'navigating': the user leaves for the target session — the inbox must
    // NOT come back over the composer they are being sent to.
    emit('close', 'navigating')
    router.push(sessionRouteLocation({ id: sessionId, project_id: projectId }, route))
}

let envelopeText = null

async function deliverToSession(session) {
    actionError.value = ''
    busy.value = true
    try {
        envelopeText = await markDelivered(session.id)
        if (envelopeText == null) return
        await prefillComposer(session.id, session.project_id)
    } catch {
        actionError.value = 'Network error — could not reach the server.'
    } finally {
        busy.value = false
    }
}

async function deliverToNewSession(projectId) {
    // Trust gate FIRST — if the user backs out, the message must stay pending.
    const gate = await ensureProjectTrust(projectId)
    if (!gate) return
    actionError.value = ''
    busy.value = true
    try {
        envelopeText = await markDelivered(null)
        if (envelopeText == null) return
        const draftId = dataStore.createDraftSession(projectId, gate.state)
        // The delivery was just recorded with NO target: the session does not
        // exist yet. Tie the message to the draft so the store can complete the
        // link once the provider creates the real session — that is what makes
        // the inbox row point at it later.
        dataStore.setDraftPeerMessage(draftId, props.messageId)
        await prefillComposer(draftId, projectId)
    } catch {
        actionError.value = 'Network error — could not reach the server.'
    } finally {
        busy.value = false
    }
}

async function refuse() {
    actionError.value = ''
    busy.value = true
    try {
        const response = await apiFetch(`/api/peer-messages/${props.messageId}/refuse/`, { method: 'POST' })
        let payload = null
        try { payload = await response.json() } catch { /* empty */ }
        if (!response.ok) {
            actionError.value = errorText(payload)
            return
        }
        emit('close')
    } catch {
        actionError.value = 'Network error — could not reach the server.'
    } finally {
        busy.value = false
        confirmingRefuse.value = false
    }
}

function onHide(event) {
    if (event.target !== dialogRef.value) return
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peer message"
        style="--width: min(720px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <wa-callout v-if="loadError" variant="danger" size="small">{{ loadError }}</wa-callout>

        <template v-if="detail">
            <!-- Header — the inbox row's header, verbatim: direction arrow,
                 peer, state, time. -->
            <div class="pr-head">
                <wa-icon
                    :name="headIcon" :label="directionLabel" :title="directionLabel"
                    class="pr-head__icon" :class="isInbound ? 'pr-head__icon--in' : 'pr-head__icon--out'"
                ></wa-icon>
                <span class="pr-head__peer">{{ peerName }}</span>
                <span class="pr-head__fill"></span>
                <wa-tag :variant="statusVariant" size="small">{{ detail.status }}</wa-tag>
                <span v-if="sentAt" class="pr-head__time">
                    <wa-relative-time
                        v-if="useRelativeTime"
                        :date.prop="sentAt" :format="relativeTimeFormat"
                        numeric="always" sync
                    ></wa-relative-time>
                    <template v-else>{{ formatDate(Math.floor(sentAt.getTime() / 1000), { smart: true }) }}</template>
                </span>
            </div>

            <!-- Message body (markdown), quoted like the inbox preview: these
                 are someone else's words, not the app's. -->
            <div class="pr-quote">
                <div class="pr-body markdown-body" v-html="renderedText"></div>
            </div>

            <!-- Attachments -->
            <MediaThumbnailGroup v-if="mediaItems.length" :items="mediaItems" />
            <p v-else-if="detail.purged && detail.attachments_meta?.length && !attachmentsLost" class="pr-purged">
                {{ detail.attachments_meta.length }} attachment(s) — bytes purged.
            </p>

            <!-- Where it went / came from. The status tag above already names
                 the state, so this line states the place, nothing else. -->
            <p v-if="localRoute" class="pr-route">
                <span class="pr-route__label">{{ localRoute.label }}</span>
                <!-- Clickable when the session is known: goes there like a
                     sidebar row, keeping the current project/workspace frame. -->
                <button
                    v-if="localRoute.sessionId"
                    type="button" class="pr-route__title pr-route__title--link"
                    :title="`Open “${localRoute.title}”`"
                    @click="openLocalSession"
                >“{{ localRoute.title }}”</button>
                <span v-else class="pr-route__title" :title="localRoute.title">“{{ localRoute.title }}”</span>
                <template v-if="localRoute.projectId">
                    <span class="pr-route__label">in</span>
                    <ProjectBadge :project-id="localRoute.projectId" class="pr-route__project" />
                </template>
            </p>
            <wa-callout v-if="attachmentsLost" variant="warning" size="small">
                Its {{ detail.attachments_meta.length }} attachment(s) were purged — a new delivery
                carries the text only.
            </wa-callout>

            <!-- Actions -->
            <template v-if="canDeliver">
                <div class="pr-note">
                    <label class="pr-note__label" for="pr-note-input">Add a message for your agent (optional)</label>
                    <wa-textarea
                        id="pr-note-input"
                        size="small" rows="2"
                        placeholder="Delivered next to the peer's message, attributed to you"
                        :value="note"
                        @input="note = $event.target.value"
                    ></wa-textarea>
                </div>

                <p class="pr-explainer">
                    <template v-if="isRedeliverable">This message was already delivered; delivering it
                    again is allowed. </template>Delivering does not send anything: the message is
                    placed in the chosen session's input (an existing one, or a new draft) — you
                    review it, adjust it if needed, and send it yourself.
                </p>

                <!-- The whole point of the dialog: filled brand, never a quiet
                     outline. The picked one stays filled, the other steps back
                     to an outline so the choice is readable. -->
                <div class="pr-actions">
                    <wa-button
                        variant="brand" :appearance="mode === 'new' ? 'outlined' : 'accent'"
                        @click="setMode('existing')"
                    >
                        <wa-icon name="comments" slot="start"></wa-icon>
                        Deliver to existing session
                    </wa-button>
                    <wa-button
                        variant="brand" :appearance="mode === 'existing' ? 'outlined' : 'accent'"
                        @click="setMode('new')"
                    >
                        <wa-icon name="plus" slot="start"></wa-icon>
                        Deliver to new session
                    </wa-button>
                    <!-- No refusal once delivered: the peer was already told
                         "delivered", and that answer is final. -->
                    <wa-button
                        v-if="isPending"
                        size="small" variant="danger" appearance="outlined"
                        class="pr-actions__refuse"
                        :disabled="busy"
                        @click="confirmingRefuse = true"
                    >Refuse</wa-button>
                </div>

                <wa-callout v-if="confirmingRefuse" variant="warning" size="small">
                    <div class="pr-confirm-body">
                        <span>Refuse this message? The sender will see it as refused.</span>
                        <span class="pr-confirm__actions">
                            <wa-button size="small" variant="danger" :disabled="busy" @click="refuse">Refuse</wa-button>
                            <wa-button size="small" appearance="outlined" @click="confirmingRefuse = false">Keep</wa-button>
                        </span>
                    </div>
                </wa-callout>

                <!-- 'New session' mode: the same project selector as every
                     new-session flow (badges, named/tree split, ws priority). -->
                <template v-if="mode === 'new'">
                    <div class="pr-new-session">
                        <wa-select
                            v-model="pickedProjectId"
                            size="small" placeholder="Pick a project…"
                        >
                            <ProjectSelectOptions
                                :projects="selectableProjects"
                                :priority-project-ids="activeWorkspace?.projectIds || null"
                                :priority-label="activeWorkspace ? `${activeWorkspace.name} projects` : null"
                                :priority-color="activeWorkspace?.color || null"
                                show-process-indicator
                                include-worktrees
                            />
                        </wa-select>
                        <wa-button
                            size="small" variant="brand"
                            :disabled="!pickedProjectId || busy"
                            @click="deliverToNewSession(pickedProjectId)"
                        >
                            <wa-icon name="pen-to-square" slot="start"></wa-icon>
                            Create draft session
                        </wa-button>
                    </div>
                </template>

                <!-- 'Existing session' mode: the sidebar's session list (same
                     order and blocks, compact rendering), minus archived and
                     drafts. Click selects; the button delivers. -->
                <template v-if="mode === 'existing'">
                    <!-- Two filters, coarse then fine: the project (the same
                         selector as 'new session', plus the current sidebar
                         frame's scopes) narrows the list, the text input
                         searches inside it. -->
                    <div class="pr-picker-filters">
                        <wa-select v-model="scopeId" size="small" class="pr-picker-scope">
                            <ProjectMark
                                v-if="scopeId !== ALL_PROJECTS_ID && !isWorkspaceProjectId(scopeId)"
                                slot="start"
                                style="--project-mark-icon-size: var(--wa-space-m); --project-mark-size: 0.75em"
                                :icon-url="scopeIconUrl"
                                :color="scopeDotColor"
                            />
                            <wa-icon
                                v-else-if="isWorkspaceProjectId(scopeId)" slot="start" name="layer-group"
                                :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"
                            ></wa-icon>
                            <wa-option :value="ALL_PROJECTS_ID">All projects</wa-option>
                            <wa-option v-if="activeWorkspace" :value="`workspace:${activeWorkspace.id}`" :label="activeWorkspace.name">
                                <span class="pr-scope-workspace">
                                    <wa-icon name="layer-group" auto-width :style="activeWorkspace.color ? { color: activeWorkspace.color } : null"></wa-icon>
                                    {{ activeWorkspace.name }}
                                </span>
                            </wa-option>
                            <wa-divider></wa-divider>
                            <ProjectSelectOptions
                                :projects="selectableProjects"
                                :priority-project-ids="activeWorkspace?.projectIds || null"
                                :priority-label="activeWorkspace ? `${activeWorkspace.name} projects` : null"
                                :priority-color="activeWorkspace?.color || null"
                                show-process-indicator
                                include-worktrees
                            />
                        </wa-select>
                        <!-- `with-clear`: the same one-click reset as the
                             sidebar's filter, whose matching this reuses. -->
                        <wa-input
                            size="small" placeholder="Filter sessions…" class="pr-picker-search"
                            with-clear
                            :value="sessionFilter"
                            @input="sessionFilter = $event.target.value"
                        ></wa-input>
                    </div>
                    <!-- The REAL sidebar row component (compact mode): identical
                         icons, colors, heights and active-session highlight.
                         Its plain left click only emits `select` (no navigation);
                         the session-actions menu is hidden via CSS below. -->
                    <div class="pr-picker">
                        <template v-for="row in sessionRows" :key="row.session.id">
                            <SidebarListSeparator v-if="row.separator" v-bind="row.separator" />
                            <SessionListItem
                                :session="row.session"
                                :active="selectedSessionId === row.session.id"
                                compact-view
                                show-project-name
                                @select="selectedSessionId = row.session.id"
                            />
                        </template>
                        <p v-if="!sessionRows.length" class="pr-empty">No matching session.</p>
                    </div>
                    <wa-button
                        size="small" variant="brand"
                        :disabled="!selectedSession || busy"
                        @click="deliverToSession(selectedSession)"
                    >
                        <wa-icon name="pen-to-square" slot="start"></wa-icon>
                        Prefill session composer
                    </wa-button>
                </template>

                <wa-callout v-if="actionError" variant="danger" size="small">{{ actionError }}</wa-callout>
            </template>
        </template>

        <div slot="footer" class="pr-footer">
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
/* ── Header, quote and routing: the inbox row's vocabulary ─────────────── */
.pr-head {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    min-width: 0;
}
.pr-head__fill { flex: 1; }
/* Inbound wears the same brand colour as the incoming-message toast,
   outbound its own hue — identical to PeerInboxRow. */
.pr-head__icon { flex-shrink: 0; }
.pr-head__icon--in { color: var(--wa-color-brand-fill-loud, var(--wa-color-brand-60)); }
.pr-head__icon--out { color: var(--wa-color-success-fill-loud, var(--wa-color-success-60)); }
.pr-head__peer {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.pr-head__time {
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
    white-space: nowrap;
    flex-shrink: 0;
}

/* The quote recipe of the markdown renderer (MarkdownContent.vue): quiet
   brand fill, left accent bar, square on the bar's side. The tint lives on
   the wrapper because `.markdown-body` paints its own background. */
.pr-quote {
    margin-bottom: var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border-start-start-radius: 0;
    border-end-start-radius: 0;
    border-inline-start: 2px solid var(--wa-color-brand-fill-loud);
    background: var(--wa-color-brand-fill-quiet);
}
.wa-dark .pr-quote { background: var(--wa-color-brand-fill-normal); }
.pr-body {
    padding: var(--wa-space-s) var(--wa-space-m);
    max-height: 40vh;
    overflow: auto;
    background: transparent;
    color: var(--wa-color-text-normal);
}
/* First and last blocks of the markdown must not push the tint open. */
.pr-body :deep(> :first-child) { margin-top: 0; }
.pr-body :deep(> :last-child) { margin-bottom: 0; }

.pr-route {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    margin: 0 0 var(--wa-space-2xs);
    font-size: 0.85rem;
    min-width: 0;
}
.pr-route__label { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.pr-route__title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 6ch;
}
/* A native <button> as an inline link: WA's native.css gives buttons a
   form-control height and centring, all of which must be reset here. */
.pr-route__title--link {
    height: auto;
    min-height: 0;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    color: var(--wa-color-brand-on-quiet);
    text-align: left;
    cursor: pointer;
}
.pr-route__title--link:hover { text-decoration: underline; }
.pr-route__project { max-width: 20ch; }

.pr-purged { color: var(--wa-color-text-quiet); font-size: 0.85rem; }
/* Three kinds of text share this dialog and must not read alike: the routing
   line is metadata (quiet, small), this is a FORM LABEL (normal colour, at
   text size), and the explainer below is a side note (quiet, italic). */
.pr-note { display: flex; flex-direction: column; gap: var(--wa-space-2xs); margin: var(--wa-space-m) 0 var(--wa-space-s); }
.pr-note__label { font-weight: var(--wa-font-weight-semibold); color: var(--wa-color-text-normal); }
.pr-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: var(--wa-space-s);
}
/* Refusing is a rare, destructive answer: kept away from the two delivery
   buttons so it is never the one clicked by reflex. */
.pr-actions__refuse { margin-inline-start: auto; }
.pr-picker-filters {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
}
/* The project scope stays secondary: the text search takes the free space. */
.pr-picker-scope { flex: 0 1 40%; min-width: 0; }
.pr-picker-search { flex: 1 1 auto; min-width: 0; }
.pr-scope-workspace {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.pr-picker {
    max-height: 30vh;
    overflow: auto;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    margin-top: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    /* SidebarListSeparator's threshold labels expand to their full wording
       ("Older than 7 days" vs "7 days +") via an anonymous container query;
       without a container ancestor the query never matches and the compact
       form shows. The picker is always wide enough for the full form. */
    container-type: inline-size;
}
/* The rows are real SessionListItems (visuals owned by the component). Only
   the session-actions "…" menu is out of place in a delivery picker. */
.pr-picker :deep(.session-menu) { display: none !important; }
.pr-empty { color: var(--wa-color-text-quiet); padding: var(--wa-space-s); margin: 0; }
.pr-new-session {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
    margin-bottom: var(--wa-space-2xs);
}
.pr-explainer {
    margin: 0 0 var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
    font-size: var(--wa-font-size-s);
}
.pr-new-session wa-select { flex: 1; min-width: 0; }
.pr-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pr-inline-callout {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
}
.pr-confirm__actions {
    display: flex;
    gap: var(--wa-space-s);
}
.pr-footer { display: flex; justify-content: flex-end; width: 100%; }
</style>
