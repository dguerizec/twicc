// frontend/src/stores/sessionSelection.js
// Pinia store for the session list multi-select mode.
//
// Ephemeral UI state (never persisted): whether the explicit multi-select
// mode is active, which session ids are selected, and the anchor used for
// Shift+click range selection. Range *computation* lives in SessionList.vue,
// which knows the visual order of the filtered list — this store only holds
// the resulting ids.

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useSessionSelectionStore = defineStore('sessionSelection', {
    state: () => ({
        /** Whether the multi-select mode is active. */
        active: false,
        /** Selected session ids. The Set is replaced (never mutated in place) on every change. */
        selectedIds: new Set(),
        /** Anchor session id for Shift+click ranges (last clicked item — plain or Ctrl/Cmd click). */
        anchorId: null,
    }),

    actions: {
        /**
         * Enter the multi-select mode. The currently open session (if any) is
         * passed as the initial anchor: it already reads as "selected" visually,
         * so a first Shift+click naturally ranges from it.
         */
        enter(anchorId = null) {
            this.active = true
            this.anchorId = anchorId
        },

        exit() {
            this.active = false
            this.selectedIds = new Set()
            this.anchorId = null
        },

        /**
         * Plain click on a session (opening it to view): it doesn't touch the
         * selection, but it becomes the anchor — a following Shift+click
         * ranges from the last clicked item, like in a file manager.
         */
        setAnchor(id) {
            this.anchorId = id
        },

        /** Ctrl/Cmd+click: toggle one session and make it the new anchor. */
        toggle(id) {
            const next = new Set(this.selectedIds)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            this.selectedIds = next
            this.anchorId = id
        },

        /**
         * Shift+click: replace the selection with a range. The anchor is
         * preserved unless explicitly provided (first Shift+click without
         * an anchor selects the clicked item alone and anchors it).
         */
        setSelection(ids, { anchor } = {}) {
            this.selectedIds = new Set(ids)
            if (anchor !== undefined) this.anchorId = anchor
        },

        /** Ctrl/Cmd+Shift+click: add a range to the selection. */
        addSelection(ids) {
            const next = new Set(this.selectedIds)
            for (const id of ids) next.add(id)
            this.selectedIds = next
        },

        /** Drop selected ids that are no longer present in the visible list. */
        prune(visibleIds) {
            if (this.anchorId && !visibleIds.has(this.anchorId)) this.anchorId = null
            if (this.selectedIds.size === 0) return
            const next = new Set([...this.selectedIds].filter(id => visibleIds.has(id)))
            if (next.size !== this.selectedIds.size) this.selectedIds = next
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useSessionSelectionStore, import.meta.hot))
}
