<script setup>
import { ref } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { toast } from '../../composables/useToast'

defineProps({ shares: { type: Array, required: true } })
const emit = defineEmits(['edit'])
const store = useSharesStore()

// Per-share expanded "Recent views" panel: id -> accesses[] (null = loading).
const accesses = ref({})

function isOutdated(s) {
    return s.kind === 'artifact' && s.source_updated_at && s.options?.snapshot_at
        && s.source_updated_at > s.options.snapshot_at
}
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
function when(iso) { try { return new Date(iso).toLocaleString() } catch { return '' } }
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
                <wa-tag v-if="isOutdated(s)" size="small" variant="warning">outdated</wa-tag>
                <button class="share-views" type="button" @click="toggleViews(s)">{{ s.view_count }} views</button>
            </div>
            <div class="share-row-actions">
                <wa-button size="small" appearance="plain" @click="copy(s)"><wa-icon name="copy"></wa-icon></wa-button>
                <wa-button v-if="isOutdated(s)" size="small" variant="warning" @click="store.propagateShare(s.id)">Propagate</wa-button>
                <wa-button size="small" appearance="plain" @click="emit('edit', s)"><wa-icon name="pen"></wa-icon></wa-button>
                <wa-button v-if="s.status !== 'revoked'" size="small" appearance="plain" @click="store.revokeShare(s.id, true)">Revoke</wa-button>
                <wa-button v-else size="small" appearance="plain" @click="store.revokeShare(s.id, false)">Unrevoke</wa-button>
                <wa-button size="small" appearance="plain" variant="danger" @click="del(s)"><wa-icon name="trash"></wa-icon></wa-button>
            </div>
            <div v-if="s.id in accesses" class="share-views-panel">
                <p v-if="accesses[s.id] === null" class="muted">Loading…</p>
                <p v-else-if="!accesses[s.id].length" class="muted">No views yet.</p>
                <ul v-else>
                    <li v-for="(a, i) in accesses[s.id]" :key="i">
                        <span class="when">{{ when(a.at) }}</span>
                        <code v-if="a.ip">{{ a.ip }}</code>
                        <span class="ua">{{ a.user_agent }}</span>
                    </li>
                </ul>
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
.share-views-panel { margin-top: 0.4rem; font-size: 0.8rem; }
.share-views-panel ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; }
.share-views-panel li { display: flex; gap: 0.6rem; align-items: baseline; }
.when { color: var(--wa-color-text-quiet); white-space: nowrap; }
.ua { color: var(--wa-color-text-quiet); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: var(--wa-color-text-quiet); }
</style>
