// useSessionLayout — wiring for the dockable layout: measure -> pure resolve -> dumb render.
//
// Mirrors the computeVisualItems pattern (raw inputs -> pure transform -> render). All layout
// rules live in the pure `resolveLayout` (utils/layoutResolver.js); this composable only:
//   - measures the session content area (useElementSize),
//   - reads the per-session layout intention from the store (assignment/collapsed/active*),
//   - feeds the resolver and exposes the renderDescription + helpers + actions.
//
// Active-tab model (agreed): the route is the single pointer to "the active/focused tab".
// `routeActiveTabId` overrides the active tab of the region that contains it; other regions
// fall back to per-group memory (activeByGroup), then the first content-bearing tab. Tab
// clicks navigate the route (handled by the caller); a watch here records the memory and
// makes a routed tab visible if its dock was collapsed.

import { computed, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { resolveLayout, DOCKS } from '../utils/layoutResolver'
import { edgeOfDock } from '../components/session/layout/dockMeta'
import { useDataStore } from '../stores/data'

const EMPTY_INTENTION = Object.freeze({
    assignment: Object.freeze({}),
    collapsed: Object.freeze([]),
    activeSide: 'left',
    activeResize: 'left',
    activeByGroup: Object.freeze({}),
})

// Stable key for a region's active-tab memory. 'center' for the center; otherwise the
// region's dockIds sorted+joined (so a merged region has its own key). Matches the prototype.
export function groupKeyOf(slots) {
    const docks = slots.map((s) => s.dockId)
    if (docks.includes('center')) return 'center'
    return docks.slice().sort().join('+')
}

export function useSessionLayout({ sessionId, containerRef, tabs, routeActiveTabId }) {
    const store = useDataStore()

    // ---- Measure ----
    const { width, height } = useElementSize(containerRef)
    const measured = computed(() => width.value > 0 && height.value > 0)

    // ---- Intention (null-safe; mutations go through store actions only) ----
    const intention = computed(() => store.getSessionLayout(sessionId.value) || EMPTY_INTENTION)

    // A present tab is "docked" when assigned to one of the six docks.
    const isDockingActive = computed(() => {
        const a = intention.value.assignment
        return tabs.value.some((t) => !t.fixedCenter && DOCKS.includes(a[t.id]))
    })

    // ---- Resolve ----
    const render = computed(() => resolveLayout({
        tabs: tabs.value,
        assignment: intention.value.assignment,
        viewport: { w: width.value, h: height.value },
        activeSide: intention.value.activeSide,
        activeResize: intention.value.activeResize,
        collapsed: intention.value.collapsed,
    }))

    // The multi-region layout actually renders only when something is docked, the area is
    // measured, and we're not in the mobile 'tabs' fallback (where everything folds back into
    // the center strip). Until then the view behaves exactly like the plain tab group.
    const dockingRendered = computed(
        () => isDockingActive.value && measured.value && render.value.mode !== 'tabs'
    )

    // ---- Active-tab resolution per region (route override -> memory -> first content) ----
    function regionActiveTabId(region) {
        const groupTabs = region.slots.flatMap((s) => s.tabs)
        if (!groupTabs.length) return null
        const routed = routeActiveTabId.value
        if (routed && groupTabs.some((t) => t.id === routed)) return routed
        const wanted = intention.value.activeByGroup[groupKeyOf(region.slots)]
        if (wanted && groupTabs.some((t) => t.id === wanted)) return wanted
        const firstContent = groupTabs.find((t) => !t.optional || t.hasContent)
        return (firstContent || groupTabs[0]).id
    }

    // The dock a tab is assigned to ('center' when unassigned).
    function dockOf(tabId) {
        return intention.value.assignment[tabId] || 'center'
    }

    // ---- Teleport target resolution (logical keys; SessionView maps key -> element) ----
    // 'center:<tabId>'  -> the tab's wa-tab-panel slot in the center group (WA hides inactive)
    // 'overlay'         -> the open overlay body (only the overlay-active tab targets it)
    // 'region:<dockId>' -> a shown dock region's body
    // null              -> nowhere visible (collapsed / overlay-but-not-active) -> hidden host
    function targetKeyForTab(tabId) {
        if (!dockingRendered.value) return `center:${tabId}`
        const dock = dockOf(tabId)
        if (dock === 'center') return `center:${tabId}`
        const edge = edgeOfDock(dock)
        if (openOverlayEdge.value === edge) {
            return overlayActiveTab.value[edge] === tabId ? 'overlay' : null
        }
        const shown = render.value.regions.some((r) => r.slots.some((s) => s.dockId === dock))
        return shown ? `region:${dock}` : null
    }

    // Whether a tool panel should be shown (vs hidden) at its destination. Center relies on
    // WA's own inactive-panel hiding; dock regions stack all their panels and need this.
    function isToolPanelVisible(tabId) {
        if (!dockingRendered.value) return true
        const dock = dockOf(tabId)
        if (dock === 'center') return true
        const edge = edgeOfDock(dock)
        if (openOverlayEdge.value === edge) return overlayActiveTab.value[edge] === tabId
        const region = render.value.regions.find((r) => r.slots.some((s) => s.dockId === dock))
        if (!region) return false
        return regionActiveTabId(region) === tabId
    }

    // ---- Overlay (peek) state — pure UI, lives here ----
    const openOverlayEdge = ref(null)     // 'left' | 'right' | 'bottom' | null
    const overlayActiveTab = ref({})      // edge -> tabId

    function openOverlay(edge, tabId) {
        if (openOverlayEdge.value === edge && (!tabId || overlayActiveTab.value[edge] === tabId)) {
            openOverlayEdge.value = null   // toggle closed
            return
        }
        openOverlayEdge.value = edge
        if (tabId) overlayActiveTab.value = { ...overlayActiveTab.value, [edge]: tabId }
    }
    function closeOverlay() { openOverlayEdge.value = null }

    // Drop a stale overlay when its edge no longer offers one.
    watch(render, (d) => {
        if (openOverlayEdge.value && !d.overlays.some((o) => o.edge === openOverlayEdge.value)) {
            openOverlayEdge.value = null
        }
    })

    // ---- Actions (intention mutations via the store) ----
    function place(tabId, dest) { store.setTabDock(sessionId.value, tabId, dest) }
    function minimize(dockIds) {
        for (const d of [].concat(dockIds)) store.minimizeDock(sessionId.value, d)
    }
    function restore(dockId) { store.restoreDock(sessionId.value, dockId) }
    function swapSide(edge) { store.setLayoutActiveSide(sessionId.value, edge) }
    function rememberActive(groupKey, tabId) {
        store.setLayoutGroupActiveTab(sessionId.value, groupKey, tabId)
    }

    // Route drives the focus: when the routed tab lives in a collapsed dock, reveal it; and
    // record it as its region's remembered tab so the region keeps it when focus moves away.
    watch(routeActiveTabId, (id) => {
        if (!id) return
        const dock = intention.value.assignment[id]
        if (!dock || !DOCKS.includes(dock)) return
        if (intention.value.collapsed.includes(dock)) restore(dock)
        const region = render.value.regions.find((r) => r.slots.some((s) => s.tabs.some((t) => t.id === id)))
        if (region) rememberActive(groupKeyOf(region.slots), id)
    })

    return {
        width, height, measured,
        render, isDockingActive, dockingRendered,
        regionActiveTabId, dockOf, groupKeyOf,
        targetKeyForTab, isToolPanelVisible,
        openOverlayEdge, overlayActiveTab, openOverlay, closeOverlay,
        place, minimize, restore, swapSide, rememberActive,
    }
}
