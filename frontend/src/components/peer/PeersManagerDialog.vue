<script setup>
/**
 * PeersManagerDialog - manage peer instances (cross-instance messaging).
 *
 * Mounted once in App.vue, opened by the `twicc:open-peers-manager`
 * CustomEvent (settings section, toasts, inbox). REST via apiFetch; the store
 * is updated by the WS broadcasts — no manual refetch after mutations.
 *
 * Rendering rule: handshake-supplied strings (remote_display_name, base_url)
 * come from an unauthenticated endpoint and are attacker-controlled — text
 * interpolation ONLY, never v-html.
 */
import { ref, computed, watch } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { useSettingsStore } from '../../stores/settings'
import { apiFetch } from '../../utils/api'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const settingsStore = useSettingsStore()
const dialogRef = ref(null)

const peerBaseUrl = computed(() => settingsStore.getPeerBaseUrl || '')
const peerDisplayName = computed(() => settingsStore.getPeerDisplayName || '')
const pendingReceived = computed(() => peersStore.peers.filter(p => p.state === 'pending_received'))
const pendingSent = computed(() => peersStore.peers.filter(p => p.state === 'pending_sent'))
const establishedPeers = computed(() => peersStore.peers.filter(p => ['active', 'broken'].includes(p.state)))

// Per-peer transient UI state (inputs, errors, busy flags), keyed by peer id.
const acceptNames = ref({})

// Local-name input for an incoming request: seeded with the locally-chosen
// alias (crossed rows) or the name the requester claims — the user just
// confirms or adjusts it before accepting. `??` keeps a deliberate clear.
function acceptNameValue(peer) {
    return acceptNames.value[peer.id] ?? (peer.name || peer.remote_display_name || '')
}
const verifyCodes = ref({})
const rowErrors = ref({})
const busy = ref({})
const confirmingRemoval = ref(null)
const renaming = ref(null)
const renameValue = ref('')

// Add-peer form state.
const addName = ref('')
const addUrl = ref('')
const addError = ref('')
const addBusy = ref(false)

// Reset transient state on every open: a Remove confirmation armed in a
// previous visit must NOT survive a dismissal — one stray click would silently
// delete a relationship and its whole history.
watch(() => props.open, (open) => {
    if (!open) return
    confirmingRemoval.value = null
    renaming.value = null
    rowErrors.value = {}
    addError.value = ''
})

function errorText(payload) {
    const errors = payload?.errors
    if (Array.isArray(errors) && errors.length) {
        return errors.map(e => e.message || e.code).join(' — ')
    }
    return payload?.error || payload?.reason || 'Request failed.'
}

const NETWORK_ERROR = 'Network error — could not reach the server.'

async function post(url, body) {
    // fetch REJECTS on network failure (server down, connection cut) — catch
    // it so the user gets an error callout instead of a silent no-op.
    try {
        const response = await apiFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        })
        let payload = null
        try { payload = await response.json() } catch { /* empty body */ }
        return { ok: response.ok, payload }
    } catch {
        return { ok: false, payload: { error: NETWORK_ERROR } }
    }
}

async function addPeer() {
    addError.value = ''
    const name = addName.value.trim()
    const url = addUrl.value.trim()
    if (!name || !url) {
        addError.value = 'Name and address are both required.'
        return
    }
    addBusy.value = true
    try {
        const { ok, payload } = await post('/api/peers/', { name, base_url: url })
        if (!ok) {
            addError.value = errorText(payload)
            return
        }
        addName.value = ''
        addUrl.value = ''
    } finally {
        addBusy.value = false
    }
}

async function acceptPeer(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const name = acceptNameValue(peer).trim() || peer.name || peer.remote_display_name
        const { ok, payload } = await post(`/api/peers/${peer.id}/accept/`, { name })
        if (!ok) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function refusePeer(peer) {
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        await post(`/api/peers/${peer.id}/refuse/`)
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function submitCode(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    const code = (verifyCodes.value[peer.id] || '').trim()
    if (!/^\d{6}$/.test(code)) {
        rowErrors.value = { ...rowErrors.value, [peer.id]: 'Enter the 6-digit code your peer reads to you.' }
        return
    }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload } = await post(`/api/peers/${peer.id}/verify/`, { code })
        if (!ok) {
            rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
        } else {
            verifyCodes.value = { ...verifyCodes.value, [peer.id]: '' }
        }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function removePeer(peer) {
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        await apiFetch(`/api/peers/${peer.id}/`, { method: 'DELETE' })
        confirmingRemoval.value = null
    } catch {
        rowErrors.value = { ...rowErrors.value, [peer.id]: NETWORK_ERROR }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

function startRename(peer) {
    renaming.value = peer.id
    renameValue.value = peer.name
}

async function _patch(peer, body) {
    try {
        const response = await apiFetch(`/api/peers/${peer.id}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        if (!response.ok) {
            let payload = null
            try { payload = await response.json() } catch { /* ignore */ }
            rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
        }
    } catch {
        rowErrors.value = { ...rowErrors.value, [peer.id]: NETWORK_ERROR }
    }
}

async function applyRename(peer) {
    const name = renameValue.value.trim()
    renaming.value = null
    if (!name || name === peer.name) return
    await _patch(peer, { name })
}

function openInbox() {
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox'))
}

// Scope dialog events to the dialog itself — nested wa-* children bubble the
// same event names (CLAUDE.md "Bubbling custom events").
// No auto-focus on open: the Add-peer form sits at the BOTTOM of the dialog,
// and focusing it would scroll the incoming request's verification code (the
// usual reason to open this dialog) out of view.
function onHide(event) {
    if (event.target !== dialogRef.value) return
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peers"
        style="--width: min(760px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <!-- Own address recap -->
        <wa-callout v-if="!peerBaseUrl" variant="warning" size="small" class="pm-block">
            Peer messaging is disabled — set your address in Settings › Peers first.
        </wa-callout>
        <p v-else class="pm-own-address">
            Your address: <code>{{ peerBaseUrl }}</code>
            <template v-if="peerDisplayName"> · shown as <strong>{{ peerDisplayName }}</strong></template>
        </p>

        <!-- Pending incoming requests -->
        <template v-if="pendingReceived.length">
            <h4 class="pm-section-title">Incoming requests</h4>
            <div v-for="peer in pendingReceived" :key="peer.id" class="pm-request">
                <div class="pm-request__claim">
                    <span class="pm-request__name">{{ peer.remote_display_name || 'unnamed instance' }}</span>
                    <span class="pm-request__url">{{ peer.base_url }}</span>
                </div>
                <div class="pm-request__code-block">
                    <span class="pm-request__code">{{ peer.verification_code }}</span>
                    <wa-tag v-if="peer.verified_at" variant="success" size="small">Verified ✓</wa-tag>
                </div>
                <p class="pm-hint">
                    Share this code with the requester over a channel you trust — accepting
                    unlocks once they confirm it.
                </p>
                <!-- Crossed handshake: this row also carries OUR outbound request.
                     Verification is symmetric — the peer shows a code too, and it
                     must be enterable HERE or the crossed flow deadlocks. -->
                <template v-if="peer.crossed">
                    <wa-tag v-if="peer.code_confirmed_at" variant="success" size="small">Code confirmed ✓</wa-tag>
                    <template v-else>
                        <div class="pm-request__actions">
                            <wa-input
                                size="small" placeholder="6-digit code" maxlength="6" inputmode="numeric"
                                :value="verifyCodes[peer.id] || ''"
                                @input="verifyCodes = { ...verifyCodes, [peer.id]: $event.target.value }"
                                @keydown.enter="submitCode(peer)"
                            ></wa-input>
                            <wa-button
                                size="small" variant="brand"
                                :disabled="busy[peer.id]"
                                @click="submitCode(peer)"
                            >Verify</wa-button>
                        </div>
                        <p class="pm-hint">You both sent a request — also enter the code your peer reads to you.</p>
                    </template>
                </template>
                <div class="pm-request__actions">
                    <wa-input
                        size="small" placeholder="Local name for this peer"
                        :value="acceptNameValue(peer)"
                        @input="acceptNames = { ...acceptNames, [peer.id]: $event.target.value }"
                    ></wa-input>
                    <wa-button
                        size="small" variant="brand"
                        :disabled="!peer.verified_at || busy[peer.id]"
                        @click="acceptPeer(peer)"
                    >Accept</wa-button>
                    <wa-button
                        size="small" variant="danger" appearance="outlined"
                        :disabled="busy[peer.id]"
                        @click="refusePeer(peer)"
                    >Refuse</wa-button>
                </div>
                <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
            </div>
        </template>

        <!-- Pending sent requests -->
        <template v-if="pendingSent.length">
            <h4 class="pm-section-title">Sent requests</h4>
            <div v-for="peer in pendingSent" :key="peer.id" class="pm-request">
                <div class="pm-request__claim">
                    <span class="pm-request__name">{{ peer.name }}</span>
                    <span class="pm-request__url">{{ peer.base_url }}</span>
                </div>
                <wa-callout
                    v-if="peer.remote_accepted_at && !peer.code_confirmed_at"
                    variant="warning" size="small"
                >
                    Accepted remotely, but your code verification hasn't completed —
                    contact your peer before trusting this relationship.
                </wa-callout>
                <template v-if="peer.code_confirmed_at">
                    <wa-tag variant="success" size="small">Code confirmed ✓</wa-tag>
                </template>
                <template v-else>
                    <div class="pm-request__actions">
                        <wa-input
                            size="small" placeholder="6-digit code" maxlength="6" inputmode="numeric"
                            :value="verifyCodes[peer.id] || ''"
                            @input="verifyCodes = { ...verifyCodes, [peer.id]: $event.target.value }"
                            @keydown.enter="submitCode(peer)"
                        ></wa-input>
                        <wa-button
                            size="small" variant="brand"
                            :disabled="busy[peer.id]"
                            @click="submitCode(peer)"
                        >Verify</wa-button>
                    </div>
                    <p class="pm-hint">Enter the code your peer reads to you.</p>
                </template>
                <div class="pm-request__actions">
                    <wa-button
                        size="small" variant="danger" appearance="outlined"
                        :disabled="busy[peer.id]"
                        @click="confirmingRemoval = peer.id"
                    >Cancel request</wa-button>
                </div>
                <wa-callout v-if="confirmingRemoval === peer.id" variant="warning" size="small">
                    <div class="pm-confirm-body">
                        <span>Remove this pending request?</span>
                        <span class="pm-confirm__actions">
                            <wa-button size="small" variant="danger" @click="removePeer(peer)">Remove</wa-button>
                            <wa-button size="small" appearance="outlined" @click="confirmingRemoval = null">Keep</wa-button>
                        </span>
                    </div>
                </wa-callout>
                <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
            </div>
        </template>

        <!-- Established peers -->
        <h4 class="pm-section-title">Peers</h4>
        <p v-if="!establishedPeers.length" class="pm-empty">No peers yet.</p>
        <div v-for="peer in establishedPeers" :key="peer.id" class="pm-peer">
            <div class="pm-peer__main">
                <template v-if="renaming === peer.id">
                    <wa-input
                        size="small" :value="renameValue"
                        @input="renameValue = $event.target.value"
                        @keydown.enter="applyRename(peer)"
                    ></wa-input>
                    <wa-button size="small" @click="applyRename(peer)">OK</wa-button>
                </template>
                <template v-else>
                    <span class="pm-peer__name">{{ peer.name || peer.remote_display_name || 'unnamed' }}</span>
                    <wa-tag :variant="peer.state === 'active' ? 'success' : 'danger'" size="small">
                        {{ peer.state }}
                    </wa-tag>
                    <wa-relative-time
                        v-if="peer.last_contact_at"
                        :date.prop="new Date(peer.last_contact_at)"
                        class="pm-peer__contact"
                    ></wa-relative-time>
                </template>
            </div>
            <div class="pm-peer__url-row">
                <span class="pm-peer__url">{{ peer.base_url }}</span>
            </div>
            <div class="pm-peer__actions">
                <wa-button size="small" appearance="plain" @click="startRename(peer)">Rename</wa-button>
                <wa-button
                    size="small" variant="danger" appearance="plain"
                    @click="confirmingRemoval = peer.id"
                >Remove</wa-button>
            </div>
            <wa-callout v-if="confirmingRemoval === peer.id" variant="warning" size="small">
                <div class="pm-confirm-body">
                    <span>
                        Removes the relationship and its message history silently — the peer is
                        not notified and will be rejected on its next send.
                    </span>
                    <span class="pm-confirm__actions">
                        <wa-button size="small" variant="danger" :disabled="busy[peer.id]" @click="removePeer(peer)">Remove</wa-button>
                        <wa-button size="small" appearance="outlined" @click="confirmingRemoval = null">Keep</wa-button>
                    </span>
                </div>
            </wa-callout>
            <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
        </div>

        <!-- Add peer -->
        <h4 class="pm-section-title">Add a peer</h4>
        <form id="pm-add-form" class="pm-add-form" @submit.prevent="addPeer">
            <wa-input
                size="small" placeholder="Name (e.g. David)"
                :value="addName" @input="addName = $event.target.value"
            ></wa-input>
            <wa-input
                size="small" placeholder="https://their-instance.example.com"
                :value="addUrl" @input="addUrl = $event.target.value"
            ></wa-input>
            <wa-button size="small" variant="brand" type="submit" :disabled="addBusy || !peerBaseUrl">
                <wa-icon name="plus" slot="start"></wa-icon>
                Send request
            </wa-button>
        </form>
        <wa-callout v-if="addError" variant="danger" size="small">{{ addError }}</wa-callout>

        <div slot="footer" class="pm-footer">
            <wa-button appearance="outlined" @click="openInbox">
                <wa-icon name="envelope" slot="start"></wa-icon>
                Inbox
            </wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.pm-block { margin-bottom: var(--wa-space-m); }
.pm-own-address {
    margin: 0 0 var(--wa-space-m);
    color: var(--wa-color-text-quiet);
}
.pm-own-address code { user-select: all; }
.pm-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-s);
    border-bottom: 2px solid var(--wa-color-surface-border);
    padding-bottom: 0.35rem;
}
.pm-section-title:first-of-type { margin-top: 0; }
.pm-empty { color: var(--wa-color-text-quiet); }

.pm-request, .pm-peer {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
}
.pm-request:last-of-type, .pm-peer:last-of-type { border-bottom: none; }

.pm-request__claim {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    min-width: 0;
}
.pm-request__name { font-weight: 600; }
.pm-request__url, .pm-peer__url {
    color: var(--wa-color-text-quiet);
    font-size: 0.85rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.pm-request__code-block {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
}
.pm-request__code {
    font-family: var(--wa-font-family-code, monospace);
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: 0.35em;
    user-select: all;
}
.pm-hint {
    margin: 0;
    font-size: 0.8rem;
    color: var(--wa-color-text-quiet);
}
.pm-request__actions, .pm-peer__actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
.pm-request__actions wa-input { flex: 1; min-width: 10rem; }

.pm-peer__main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
}
.pm-peer__name { font-weight: 600; }
.pm-peer__contact {
    margin-left: auto;
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
}
.pm-peer__url-row { display: flex; gap: var(--wa-space-xs); min-width: 0; }

.pm-add-form {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
.pm-add-form wa-input { flex: 1; min-width: 12rem; }

.pm-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}

/* Confirmation callouts: breathing room between the text and the buttons. */
.pm-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pm-confirm__actions {
    display: flex;
    gap: var(--wa-space-s);
}
</style>
