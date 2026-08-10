<script setup>
/**
 * PeerInboxDialog — the peer inbox: pending requests, pending inbound
 * messages, recent history. A missed toast must never lose a message — this
 * panel (plus the badge button) is the persistent surface. Every message row
 * is clickable, history included: a resolved message reopens read-only, and a
 * delivered one can be routed again from there.
 *
 * Mounted once in App.vue, opened by `twicc:open-peer-inbox` (optionally with
 * `detail.messageId` → App.vue opens the review dialog directly).
 */
import { ref, computed } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { useDataStore } from '../../stores/data'
import ProjectBadge from '../project/ProjectBadge.vue'

defineProps({ open: Boolean })
const emit = defineEmits(['close', 'review'])

const peersStore = usePeersStore()
const dataStore = useDataStore()
const dialogRef = ref(null)

const pendingRequests = computed(() => peersStore.pendingRequests)
const pendingMessages = computed(() => peersStore.pendingInboundMessages)
const history = computed(() =>
    peersStore.messages
        .filter(m => !(m.direction === 'in' && m.status === 'pending'))
        .slice(0, 50)
        .map(message => ({ ...message, target: deliveryTarget(message) }))
)

function statusVariant(status) {
    if (status === 'delivered') return 'success'
    if (status === 'pending') return 'neutral'
    return 'danger'
}

/** Where an inbound message ended up — the cue that tells the user they picked
 *  the wrong session, and the reason history rows are re-openable. The project
 *  badge comes along: a session title alone does not identify the target. */
function deliveryTarget(message) {
    if (message.direction !== 'in' || !message.delivered_to_session_id) return null
    const session = dataStore.getSession(message.delivered_to_session_id)
    return {
        title: session?.title || message.delivered_to_session_id,
        projectId: session?.project_id || null,
    }
}

function attachmentSummary(message) {
    const count = message.attachments_meta?.length || 0
    if (!count) return ''
    const base = `${count} attachment${count > 1 ? 's' : ''}`
    return message.purged ? `${base} (purged)` : base
}

function openManager() {
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))
}

function review(message) {
    emit('review', message.id)
}

function onHide(event) {
    if (event.target !== dialogRef.value) return
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peer inbox"
        style="--width: min(680px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <template v-if="pendingRequests.length">
            <h4 class="pi-section-title">Pending requests</h4>
            <button
                v-for="peer in pendingRequests" :key="peer.id"
                type="button" class="pi-row pi-row--clickable"
                @click="openManager"
            >
                <wa-icon name="user-plus" class="pi-row__icon"></wa-icon>
                <span class="pi-row__title">{{ peer.remote_display_name || 'unnamed instance' }}</span>
                <span class="pi-row__meta">{{ peer.base_url }}</span>
                <span class="pi-row__hint">Review in manager</span>
            </button>
        </template>

        <h4 class="pi-section-title">Pending messages</h4>
        <p v-if="!pendingMessages.length" class="pi-empty">No pending message.</p>
        <button
            v-for="message in pendingMessages" :key="message.id"
            type="button" class="pi-row pi-row--clickable"
            @click="review(message)"
        >
            <wa-icon name="envelope" class="pi-row__icon"></wa-icon>
            <span class="pi-row__title">{{ peersStore.peerLabel(message.peer_id) }}</span>
            <span class="pi-row__preview">{{ message.text_preview }}</span>
            <span v-if="attachmentSummary(message)" class="pi-row__meta">{{ attachmentSummary(message) }}</span>
            <wa-relative-time
                v-if="message.created_at"
                :date.prop="new Date(message.created_at)"
                class="pi-row__time"
            ></wa-relative-time>
        </button>

        <template v-if="history.length">
            <h4 class="pi-section-title">History</h4>
            <!-- Re-openable: a resolved message stays readable, and a delivered
                 one can be routed again (wrong session picked, draft cleared). -->
            <button
                v-for="message in history" :key="message.id"
                type="button" class="pi-row pi-row--clickable"
                @click="review(message)"
            >
                <wa-icon
                    :name="message.direction === 'in' ? 'arrow-down' : 'arrow-up'"
                    class="pi-row__icon"
                ></wa-icon>
                <span class="pi-row__title">{{ peersStore.peerLabel(message.peer_id) }}</span>
                <span class="pi-row__preview">{{ message.text_preview }}</span>
                <span v-if="message.target" class="pi-row__meta pi-row__target">
                    <wa-icon name="arrow-right-long" class="pi-row__target-arrow"></wa-icon>
                    <ProjectBadge v-if="message.target.projectId" :project-id="message.target.projectId" />
                    <span class="pi-row__target-title">{{ message.target.title }}</span>
                </span>
                <span v-if="attachmentSummary(message)" class="pi-row__meta">{{ attachmentSummary(message) }}</span>
                <wa-tag :variant="statusVariant(message.status)" size="small">{{ message.status }}</wa-tag>
                <wa-relative-time
                    v-if="message.resolved_at || message.created_at"
                    :date.prop="new Date(message.resolved_at || message.created_at)"
                    class="pi-row__time"
                ></wa-relative-time>
            </button>
        </template>

        <div slot="footer" class="pi-footer">
            <wa-button appearance="outlined" @click="openManager">
                <wa-icon name="user-group" slot="start"></wa-icon>
                Manage peers
            </wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.pi-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-s);
    border-bottom: 2px solid var(--wa-color-surface-border);
    padding-bottom: 0.35rem;
}
.pi-section-title:first-of-type { margin-top: 0; }
.pi-empty { color: var(--wa-color-text-quiet); }

.pi-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: var(--wa-space-xs) 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
    min-width: 0;
    background: none;
    border-left: none;
    border-right: none;
    border-top: none;
    color: inherit;
    font: inherit;
    text-align: left;
}
.pi-row:last-of-type { border-bottom: none; }
.pi-row--clickable { cursor: pointer; }
.pi-row--clickable:hover { background: var(--wa-color-surface-raised); }

.pi-row__icon { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.pi-row__title { font-weight: 600; flex-shrink: 0; }
.pi-row__preview,
.pi-row__meta {
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.pi-row__preview { flex: 1; }
.pi-row__target {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    max-width: 40%;
    flex-shrink: 0;
}
.pi-row__target-arrow { font-size: 0.75rem; flex-shrink: 0; }
.pi-row__target-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pi-row__hint { margin-left: auto; color: var(--wa-color-text-quiet); font-size: 0.8rem; flex-shrink: 0; }
.pi-row__time { margin-left: auto; color: var(--wa-color-text-quiet); font-size: 0.8rem; flex-shrink: 0; white-space: nowrap; }

.pi-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}
</style>
