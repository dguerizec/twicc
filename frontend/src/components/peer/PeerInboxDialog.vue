<script setup>
/**
 * PeerInboxDialog — the peer inbox: pending requests, pending inbound
 * messages, recent history. A missed toast must never lose a message — this
 * panel (plus the badge button) is the persistent surface.
 *
 * Mounted once in App.vue, opened by `twicc:open-peer-inbox` (optionally with
 * `detail.messageId` → App.vue opens the review dialog directly).
 */
import { ref, computed } from 'vue'
import { usePeersStore } from '../../stores/peers'

defineProps({ open: Boolean })
const emit = defineEmits(['close', 'review'])

const peersStore = usePeersStore()
const dialogRef = ref(null)

const pendingRequests = computed(() => peersStore.pendingRequests)
const pendingMessages = computed(() => peersStore.pendingInboundMessages)
const history = computed(() =>
    peersStore.messages
        .filter(m => !(m.direction === 'in' && m.status === 'pending'))
        .slice(0, 50)
)

function statusVariant(status) {
    if (status === 'delivered') return 'success'
    if (status === 'pending') return 'neutral'
    return 'danger'
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
            <div v-for="message in history" :key="message.id" class="pi-row">
                <wa-icon
                    :name="message.direction === 'in' ? 'arrow-down' : 'arrow-up'"
                    class="pi-row__icon"
                ></wa-icon>
                <span class="pi-row__title">{{ peersStore.peerLabel(message.peer_id) }}</span>
                <span class="pi-row__preview">{{ message.text_preview }}</span>
                <span v-if="attachmentSummary(message)" class="pi-row__meta">{{ attachmentSummary(message) }}</span>
                <wa-tag :variant="statusVariant(message.status)" size="small">{{ message.status }}</wa-tag>
                <wa-relative-time
                    v-if="message.resolved_at || message.created_at"
                    :date.prop="new Date(message.resolved_at || message.created_at)"
                    class="pi-row__time"
                ></wa-relative-time>
            </div>
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
.pi-row__hint { margin-left: auto; color: var(--wa-color-text-quiet); font-size: 0.8rem; flex-shrink: 0; }
.pi-row__time { margin-left: auto; color: var(--wa-color-text-quiet); font-size: 0.8rem; flex-shrink: 0; white-space: nowrap; }

.pi-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}
</style>
