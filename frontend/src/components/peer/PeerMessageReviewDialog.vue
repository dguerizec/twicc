<script setup>
/**
 * PeerMessageReviewDialog — read a pending peer message and route it.
 *
 * The receiving-side human gate (design §6): the full message (markdown +
 * attachments) is read here, then delivered to an existing session, to a new
 * session in a picked project, or refused. The message itself is never
 * editable — an optional recipient note is injected alongside instead.
 *
 * The full payload (base64 blobs) is fetched from REST on open; the store
 * only ever holds summaries.
 */
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePeersStore } from '../../stores/peers'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { renderMarkdown } from '../../utils/markdown'
import { sdkBlockToMediaItem } from '../../utils/fileUtils'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { computeSidebarSessionBlocks } from '../../utils/sidebarSessions'
import { dateBucketSeparator } from '../../utils/datePresets'
import { matchQuery } from '../../utils/textFilter'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import { ensureProjectTrust } from '../../composables/useTrustGate'
import MediaThumbnailGroup from '../media/MediaThumbnailGroup.vue'
import ProjectSelectOptions from '../project/ProjectSelectOptions.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import SidebarListSeparator from '../sidebar/SidebarListSeparator.vue'

const props = defineProps({
    open: Boolean,
    messageId: { type: [Number, String], default: null },
})
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const dataStore = useDataStore()
const router = useRouter()
const route = useRoute()

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
// 'existing' mode: a click only SELECTS (highlight); the explicit Deliver
// button sends — no accidental one-click delivery.
const selectedSessionId = ref(null)

const peerName = computed(() => peersStore.peerLabel(detail.value?.peer_id))
const origin = computed(() => detail.value?.origin || {})
const isPending = computed(() => detail.value?.direction === 'in' && detail.value?.status === 'pending')

const mediaItems = computed(() => {
    const payload = detail.value?.payload
    if (!payload) return []
    return [...(payload.images || []), ...(payload.documents || [])]
        .map(sdkBlockToMediaItem)
        .filter(Boolean)
})

const workspacesStore = useWorkspacesStore()

// 'New session' project picker: the same wa-select + ProjectSelectOptions the
// other new-session flows use (badges, named/tree split, workspace priority) —
// non-stale, non-archived, worktrees excluded like every project selector.
const selectableProjects = computed(() =>
    dataStore.getListableProjects.filter(p => !p.archived && !p.stale)
)

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

// 'Existing session' picker: the sidebar's session list verbatim — same
// blocks, same order (pinned/active/natural + date buckets) — minus archived
// (never a delivery target here) and drafts (no real session to inject into).
const sessionRows = computed(() => {
    if (mode.value !== 'existing') return []
    const blocks = computeSidebarSessionBlocks({
        data: dataStore,
        workspaces: workspacesStore,
        effectiveProjectId: effectiveProjectId.value,
        activeWorkspaceId: activeWorkspaceId.value,
        sessionId: null,
        showArchived: false,
        showArchivedProjects: false,
        showActiveAcrossFilters: true,
    })
    const processStates = dataStore.processStates
    const nowMs = Date.now()
    const entries = []
    const push = (session, sectionKey, separator) => {
        if (session.draft || session.archived) return
        entries.push({ session, sectionKey, separator })
    }
    for (const s of blocks.crossFilterPinned) push(s, 'xpinned', { label: 'Pinned elsewhere' })
    for (const s of blocks.crossFilterActive) push(s, 'xactive', { label: 'Active elsewhere' })
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
    selectedSessionId.value = null
    confirmingRefuse.value = false
    try {
        const response = await apiFetch(`/api/peer-messages/${props.messageId}/`)
        if (!response.ok) {
            loadError.value = 'Could not load the message.'
            return
        }
        detail.value = await response.json()
    } catch {
        // fetch rejects on network failure — never leave the dialog blank.
        loadError.value = 'Could not load the message — is the server reachable?'
        return
    }
    renderedText.value = await renderMarkdown(detail.value?.payload?.text || '')
}, { immediate: true })

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
        body: JSON.stringify({ session_id: sessionId || undefined, note: note.value }),
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
    emit('close')
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
            <!-- Provenance -->
            <p class="pr-provenance">
                <strong>{{ peerName }}</strong>
                <template v-if="origin.session_title"> · session "{{ origin.session_title }}"</template>
                <template v-if="origin.sent_at">
                    · <wa-relative-time :date.prop="new Date(origin.sent_at)"></wa-relative-time>
                </template>
            </p>

            <!-- Message body (markdown) -->
            <div class="pr-body markdown-content" v-html="renderedText"></div>

            <!-- Attachments -->
            <MediaThumbnailGroup v-if="mediaItems.length" :items="mediaItems" />
            <p v-else-if="detail.purged && detail.attachments_meta?.length" class="pr-purged">
                {{ detail.attachments_meta.length }} attachment(s) — bytes purged.
            </p>

            <!-- Already resolved -->
            <wa-callout v-if="!isPending" variant="neutral" size="small">
                This message was already {{ detail.status }}.
            </wa-callout>

            <!-- Actions -->
            <template v-else>
                <div class="pr-note">
                    <label class="pr-note__label">Recipient note (optional)</label>
                    <wa-textarea
                        size="small" rows="2"
                        placeholder="Injected alongside the message, attributed to you"
                        :value="note"
                        @input="note = $event.target.value"
                    ></wa-textarea>
                </div>

                <p class="pr-explainer">
                    Delivering does not send anything: the message is placed in the chosen
                    session's input (an existing one, or a new draft) — you review it,
                    adjust it if needed, and send it yourself.
                </p>

                <div class="pr-actions">
                    <wa-button
                        size="small" :variant="mode === 'existing' ? 'brand' : 'neutral'"
                        appearance="outlined"
                        @click="mode = mode === 'existing' ? null : 'existing'; pickedProjectId = ''; selectedSessionId = null"
                    >Deliver to existing session</wa-button>
                    <wa-button
                        size="small" :variant="mode === 'new' ? 'brand' : 'neutral'"
                        appearance="outlined"
                        @click="mode = mode === 'new' ? null : 'new'; pickedProjectId = ''; selectedSessionId = null"
                    >Deliver to new session</wa-button>
                    <wa-button
                        size="small" variant="danger" appearance="outlined"
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
                    <wa-input
                        size="small" placeholder="Filter sessions…"
                        :value="sessionFilter"
                        @input="sessionFilter = $event.target.value"
                    ></wa-input>
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
.pr-provenance {
    margin: 0 0 var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
.pr-body {
    padding: var(--wa-space-s);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    margin-bottom: var(--wa-space-s);
    max-height: 40vh;
    overflow: auto;
}
.pr-purged { color: var(--wa-color-text-quiet); font-size: 0.85rem; }
.pr-note { display: flex; flex-direction: column; gap: var(--wa-space-2xs); margin: var(--wa-space-s) 0; }
.pr-note__label { font-size: 0.85rem; color: var(--wa-color-text-quiet); }
.pr-actions {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    margin-bottom: var(--wa-space-s);
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
