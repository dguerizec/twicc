<script setup>
import { ref } from 'vue'
import { useSharesStore } from '../../stores/shares'
import ShareListPanel from './ShareListPanel.vue'
import ShareDialog from './ShareDialog.vue'

defineProps({ open: Boolean })
const emit = defineEmits(['close'])
const store = useSharesStore()
const editing = ref(null)
const dialogRef = ref(null)

function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open" label="Shared links"
               style="--width: min(720px, calc(100vw - 2rem))" @wa-hide="onHide">
        <ShareListPanel :shares="store.list" @edit="editing = $event" />
        <ShareDialog v-if="editing" :open="!!editing" :kind="editing.kind"
                     :session-id="editing.session_id" :bookmark-id="editing.bookmark_id"
                     :allowed-hosts="editing.allowed_hosts || {}"
                     :default-title="editing.target_title || editing.target_name || ''"
                     :edit="editing" @close="editing = null" />
        <div slot="footer" class="dialog-footer"><wa-button @click="emit('close')">Close</wa-button></div>
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
