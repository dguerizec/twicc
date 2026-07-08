import { defineStore } from 'pinia'
import { apiFetch } from '../utils/api'

export const useSharesStore = defineStore('shares', {
    state: () => ({ shares: {} }),   // id -> serialized share
    getters: {
        list: (s) => Object.values(s.shares).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')),
        forSession: (s) => (sessionId) => Object.values(s.shares).filter(x => x.kind === 'session' && x.session_id === sessionId),
        forBookmark: (s) => (bookmarkId) => Object.values(s.shares).filter(x => x.kind === 'artifact' && x.bookmark_id === bookmarkId),
        activeCountForSession: (s) => (sessionId) =>
            Object.values(s.shares).filter(x => x.kind === 'session' && x.session_id === sessionId && x.status === 'active').length,
        activeCountForBookmark: (s) => (bookmarkId) =>
            Object.values(s.shares).filter(x => x.kind === 'artifact' && x.bookmark_id === bookmarkId && x.status === 'active').length,
    },
    actions: {
        setShares(list) { const next = {}; for (const s of list || []) next[s.id] = s; this.shares = next },
        upsertShare(share) { this.shares[share.id] = share },
        removeShare(id) { delete this.shares[id] },
        async loadShares() {
            const res = await apiFetch('/api/shares/')
            if (res.ok) this.setShares((await res.json()).shares)
        },
        async createShare(body) {
            const res = await apiFetch('/api/shares/', {
                method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
            })
            if (!res.ok) throw await res.json().catch(() => ({ error: 'create failed' }))
            const share = await res.json(); this.upsertShare(share); return share
        },
        async patchShare(id, fields) {
            const res = await apiFetch(`/api/shares/${id}/`, {
                method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(fields),
            })
            if (!res.ok) throw await res.json().catch(() => ({ error: 'update failed' }))
            const share = await res.json(); this.upsertShare(share); return share
        },
        async revokeShare(id, revoked = true) {
            const res = await apiFetch(`/api/shares/${id}/${revoked ? 'revoke' : 'unrevoke'}/`, { method: 'POST' })
            if (res.ok) this.upsertShare(await res.json())
        },
        async propagateShare(id) {
            const res = await apiFetch(`/api/shares/${id}/propagate/`, { method: 'POST' })
            if (res.ok) this.upsertShare(await res.json())
        },
        async deleteShare(id) {
            const res = await apiFetch(`/api/shares/${id}/`, { method: 'DELETE' })
            if (res.ok) this.removeShare(id)
        },
        async fetchAccesses(id) {
            const res = await apiFetch(`/api/shares/${id}/accesses/`)
            return res.ok ? (await res.json()).accesses : []
        },
    },
})
