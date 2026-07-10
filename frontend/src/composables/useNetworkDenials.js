// Owner-side "you have something to decide" signal for an artifact's broker
// network access. A shared (or previewed) artifact may try to reach hosts the
// owner has neither allowed nor denied yet; those refused-but-undecided hosts
// are what the bookmark / edit affordances warn about.
//
// The denial provenance rows live behind a per-bookmark endpoint (same one the
// bookmark dialog's Network access list uses), fetched on demand and refreshed
// on the live `twicc:network-denials-updated` ping. Each affordance shows a
// single artifact at a time, so a per-bookmark fetch is cheap.

import { ref, computed, watch, onMounted, onBeforeUnmount, toValue } from 'vue'
import { apiFetch } from '../utils/api'

/**
 * @param {import('vue').Ref|(() => object|null)} bookmarkRef  The current bookmark
 *   object (or null). Read for its id + live `allowed_hosts` / `denied_hosts`.
 * @returns {{ pendingCount: import('vue').ComputedRef<number>, refresh: () => Promise<void> }}
 */
export function useNetworkDenials(bookmarkRef) {
    const denials = ref([])
    const bookmarkId = () => toValue(bookmarkRef)?.id ?? null

    async function refresh() {
        const id = bookmarkId()
        if (id == null) { denials.value = []; return }
        try {
            const res = await apiFetch(`/api/artifact-bookmarks/${id}/network-denials/`)
            denials.value = res.ok ? ((await res.json()).denials || []) : []
        } catch { /* keep the prior list on a transient failure */ }
    }

    // Distinct refused hosts that are neither allowed nor denied AND that a
    // VIEWER actually hit — the ones still awaiting an owner decision. An
    // owner-only refusal (share === null, hit in the owner's own preview where
    // they already saw it and chose deny) must not raise the icon by itself; it
    // still shows in the dialog's list. Recomputes when the denial rows change or
    // the bookmark's allow/deny dicts do (both sync live), so allowing/denying a
    // host clears it from the count without a manual refresh.
    const pendingCount = computed(() => {
        const b = toValue(bookmarkRef)
        if (!b) return 0
        const allowed = b.allowed_hosts || {}
        const denied = b.denied_hosts || {}
        const pending = new Set()
        for (const d of denials.value) {
            if (d.host_key in allowed || d.host_key in denied) continue
            if (d.share) pending.add(d.host_key) // viewer-side only
        }
        return pending.size
    })

    function onPing(e) { if (e.detail?.bookmarkId === bookmarkId()) refresh() }

    onMounted(() => {
        refresh()
        window.addEventListener('twicc:network-denials-updated', onPing)
    })
    onBeforeUnmount(() => window.removeEventListener('twicc:network-denials-updated', onPing))
    watch(bookmarkId, refresh)

    return { pendingCount, refresh }
}
