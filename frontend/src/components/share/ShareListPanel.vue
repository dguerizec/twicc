<script setup>
import { ref, useId } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { isShareOutdated } from '../../utils/shareStatus'
import { toast } from '../../composables/useToast'
import AppTooltip from '../ui/AppTooltip.vue'
import AccessLogList from './AccessLogList.vue'

defineProps({ shares: { type: Array, required: true } })
const emit = defineEmits(['edit'])
const store = useSharesStore()

// Per-instance prefix so row-action tooltip ids stay unique even if two panels
// (the global manager and a per-target dialog) render the same share at once.
const uid = useId()

// Per-share expanded "Recent views" panel: id -> accesses[] (null = loading).
const accesses = ref({})
function copy(s) {
    const url = shareAbsoluteUrl(s)
    if (!url) { toast.error?.('Configure a share host in Settings → Sharing first.'); return }
    navigator.clipboard.writeText(url); toast.success('Share URL copied')
}
async function del(s) {
    if (!confirm('Delete this share link? It cannot be undone.')) return
    await store.deleteShare(s.id)
}
async function toggleViews(s) {
    if (s.id in accesses.value) { delete accesses.value[s.id]; return }
    accesses.value[s.id] = null
    accesses.value[s.id] = await store.fetchAccesses(s.id)
}
</script>

<template>
    <div class="share-list">
        <div v-for="s in shares" :key="s.id" class="share-row">
            <div class="share-row-main">
                <wa-tag size="small" :variant="s.status === 'active' ? 'success' : (s.status === 'expired' ? 'warning' : 'neutral')">
                    {{ s.status }}
                </wa-tag>
                <span class="share-label">{{ s.label || '(no label)' }}</span>
                <wa-tag v-if="s.has_password" size="small" variant="neutral"><wa-icon name="lock"></wa-icon></wa-tag>
                <wa-tag v-if="isShareOutdated(s)" size="small" variant="warning">outdated</wa-tag>
                <button class="share-views" type="button" @click="toggleViews(s)">{{ s.view_count }} views</button>
            </div>
            <!-- Recent views expand right under their trigger, above the actions. -->
            <div v-if="s.id in accesses" class="share-views-panel">
                <p v-if="accesses[s.id] === null" class="muted">Loading…</p>
                <p v-else-if="!accesses[s.id].length" class="muted">No views yet.</p>
                <AccessLogList v-else :entries="accesses[s.id]" />
            </div>
            <div class="share-row-actions">
                <wa-button :id="`${uid}-copy-${s.id}`" size="small" appearance="plain" @click="copy(s)"><wa-icon name="copy"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-copy-${s.id}`">Copy share URL</AppTooltip>
                <wa-button v-if="isShareOutdated(s)" :id="`${uid}-push-${s.id}`" size="small" variant="warning" @click="store.propagateShare(s.id)">Push update</wa-button>
                <AppTooltip v-if="isShareOutdated(s)" :for="`${uid}-push-${s.id}`">Push the current files to this link</AppTooltip>
                <wa-button :id="`${uid}-edit-${s.id}`" size="small" appearance="plain" @click="emit('edit', s)"><wa-icon name="pen"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-edit-${s.id}`">Edit share settings</AppTooltip>
                <wa-button v-if="s.status !== 'revoked'" :id="`${uid}-revoke-${s.id}`" size="small" appearance="plain" @click="store.revokeShare(s.id, true)">Revoke</wa-button>
                <AppTooltip v-if="s.status !== 'revoked'" :for="`${uid}-revoke-${s.id}`">Disable this link (viewers get a 404)</AppTooltip>
                <wa-button v-if="s.status === 'revoked'" :id="`${uid}-unrevoke-${s.id}`" size="small" appearance="plain" @click="store.revokeShare(s.id, false)">Unrevoke</wa-button>
                <AppTooltip v-if="s.status === 'revoked'" :for="`${uid}-unrevoke-${s.id}`">Re-enable this link</AppTooltip>
                <wa-button :id="`${uid}-del-${s.id}`" size="small" appearance="plain" variant="danger" @click="del(s)"><wa-icon name="trash"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-del-${s.id}`">Delete this link permanently</AppTooltip>
            </div>
        </div>
        <p v-if="!shares.length" class="share-empty muted">No share links yet.</p>
    </div>
</template>

<style scoped>
.share-row { padding: 0.5rem 0; border-bottom: 1px solid var(--wa-color-surface-border); }
.share-row-main { display: flex; align-items: center; gap: 0.5rem; }
.share-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-views { background: none; border: none; color: var(--wa-color-text-quiet); cursor: pointer; font-size: 0.85rem; text-decoration: underline dotted; }
.share-row-actions { display: flex; gap: 0.25rem; margin-top: 0.35rem; flex-wrap: wrap; }
/* The fetched log is capped server-side (newest 200); AccessLogList itself
   caps the panel height so a busy link scrolls instead of blowing up the dialog. */
.share-views-panel { margin-top: 0.4rem; font-size: 0.8rem; }
.muted { color: var(--wa-color-text-quiet); }
</style>
