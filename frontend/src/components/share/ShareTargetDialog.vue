<script setup>
// Per-target share manager: lists every share of one session / artifact and lets
// the owner manage them or create another. Opened by the share buttons when the
// target already has links (an empty target jumps straight to the create dialog).
// Reuses ShareListPanel (the same list rendered by the global ShareManagerDialog)
// and hosts a nested ShareDialog for both create and edit.
import { ref, computed, watch } from 'vue'
import { useSharesStore } from '../../stores/shares'
import ShareListPanel from './ShareListPanel.vue'
import ShareDialog from './ShareDialog.vue'

const props = defineProps({
    open: Boolean,
    kind: { type: String, required: true },          // 'session' | 'artifact'
    sessionId: { type: String, default: null },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },  // artifact: hosts viewers reach
    defaultTitle: { type: String, default: '' },     // target session title / bookmark name
})
const emit = defineEmits(['close'])
const store = useSharesStore()
const dialogRef = ref(null)

// The shares targeting this session / bookmark, newest first (mirrors the store's
// `list` getter ordering).
const shares = computed(() => {
    const list = props.kind === 'artifact'
        ? store.forBookmark(props.bookmarkId)
        : store.forSession(props.sessionId)
    return [...list].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
})

// The nested ShareDialog serves both creating a new link and editing an existing
// one — `editing` holds the share being edited (null = create mode).
const childOpen = ref(false)
const editing = ref(null)
function openCreate() { editing.value = null; childOpen.value = true }
function openEdit(s) { editing.value = s; childOpen.value = true }
function closeChild() { childOpen.value = false; editing.value = null }
// Closing the manager must drop any pending child state so a later reopen doesn't
// flash a stale create/edit dialog.
watch(() => props.open, (o) => { if (!o) closeChild() })

// Guard bubbling wa-hide from the nested ShareDialog: only the manager's own close
// emits (see the WA nested-event trap in CLAUDE.md).
function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open"
               :label="defaultTitle ? `Shared links — ${defaultTitle}` : 'Shared links'"
               style="--width: min(720px, calc(100vw - 2rem))" @wa-hide="onHide">
        <ShareListPanel :shares="shares" @edit="openEdit" />
        <ShareDialog v-if="childOpen" :open="childOpen" :kind="kind"
                     :session-id="sessionId" :bookmark-id="bookmarkId"
                     :allowed-hosts="allowedHosts" :default-title="defaultTitle"
                     :edit="editing" @close="closeChild" />
        <div slot="footer" class="dialog-footer">
            <wa-button variant="brand" @click="openCreate">
                <wa-icon slot="start" name="plus"></wa-icon>Create new share
            </wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
</style>
