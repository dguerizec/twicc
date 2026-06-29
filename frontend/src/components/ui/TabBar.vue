<script setup>
/**
 * TabBar - Unified wrapper around <wa-tab-group>.
 *
 * A transparent, pre-styled tab group: write the exact same `<wa-tab slot="nav">`
 * and `<wa-tab-panel>` children you would put in a raw <wa-tab-group>, and they
 * pass straight through. The wrapper only carries our shared defaults so the
 * compact size no longer has to be re-declared at every call site.
 *
 * Behaviour added on top, both only when the tabs overflow (WA shows its chevrons):
 *  - a vertical mouse wheel over the tab strip scrolls it horizontally (touch and
 *    horizontal trackpad already pan via the native overflow-x);
 *  - the active tab is kept in view when the tab list is reordered — WA only
 *    re-scrolls on an `active` change, not when the active tab merely moves.
 *
 * Usage:
 *   <TabBar :active="activeId" @wa-tab-show="onShow">
 *     <wa-tab slot="nav" panel="a">A</wa-tab>
 *     <wa-tab-panel name="a">…</wa-tab-panel>
 *   </TabBar>
 *
 * All attributes, classes and listeners are forwarded verbatim to the underlying
 * <wa-tab-group> (inheritAttrs is off + v-bind="$attrs"; Vue still merges class/style).
 *
 * Exposes:
 *   - el: the native <wa-tab-group> element, for the rare consumer that needs the
 *     shadow root (e.g. setting a title on ::part(nav)). A template `ref` on this
 *     component yields the Vue instance, not the element — reach the host via `.el`.
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'

defineOptions({ inheritAttrs: false })

const el = ref(null)
defineExpose({ el })

// WA's internal `.nav` scroller (the same element its chevrons drive). All our
// scroll handling is scoped to it, so a wheel/scroll over a tab panel is never
// hijacked. It's a WA internal, so every path no-ops when it's absent.
let navEl = null

// ── Wheel → horizontal scroll ──────────────────────────────────────────────────
// Only a vertical wheel is translated, and only when the strip overflows — otherwise
// the page scrolls as usual. Horizontal wheel (deltaX / trackpad) is left to native.
function onWheel(event) {
    if (event.deltaY === 0 || event.shiftKey) return
    if (!navEl || navEl.scrollWidth <= navEl.clientWidth) return
    navEl.scrollLeft += event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY
    event.preventDefault()
}

// ── Keep the active tab visible across tab-list changes ─────────────────────────
// WA only re-scrolls to the active tab when its `active` property changes (its
// `watch("active")` → scrollIntoView). A pure REORDER — the active tab keeps the
// same panel but moves in the DOM (e.g. a workflow run jumping to the front once it
// re-sorts) — fires no such scroll, leaving the tab parked off-screen. We watch the
// slotted tab list and re-apply WA's own scrollIntoView logic. `el.active` is read
// live at scroll time, so this only ever FOLLOWS the active tab (chosen by the call
// site), never picks one: when a freshly-added tab is also activated, WA already
// scrolled to it and this is a no-op.
let listObserver = null
let pendingFrame = 0

function ensureActiveVisible() {
    pendingFrame = 0
    if (!navEl || navEl.scrollWidth <= navEl.clientWidth) return
    const active = el.value?.active
    if (!active) return
    const tab = [...el.value.querySelectorAll(':scope > wa-tab')].find((t) => t.panel === active)
    if (!tab) return
    // WA's own algorithm: align whichever near edge is out of view, so the tab shows
    // fully when it fits and otherwise as much as possible.
    const offsetLeft = tab.getBoundingClientRect().left - navEl.getBoundingClientRect().left + navEl.scrollLeft
    const minX = navEl.scrollLeft
    const maxX = navEl.scrollLeft + navEl.offsetWidth
    if (offsetLeft < minX) {
        navEl.scrollTo({ left: offsetLeft, behavior: 'smooth' })
    } else if (offsetLeft + tab.clientWidth > maxX) {
        navEl.scrollTo({ left: offsetLeft - navEl.offsetWidth + tab.clientWidth, behavior: 'smooth' })
    }
}

function scheduleEnsureActiveVisible() {
    if (pendingFrame) return
    pendingFrame = requestAnimationFrame(ensureActiveVisible)
}

onMounted(async () => {
    if (el.value?.updateComplete) await el.value.updateComplete
    navEl = el.value?.shadowRoot?.querySelector('.nav')
    navEl?.addEventListener('wheel', onWheel, { passive: false })
    // The wa-tab elements are direct light-DOM children, so a keyed reorder surfaces
    // as childList mutations here. Coalesce a burst into one rAF (lets layout settle).
    listObserver = new MutationObserver(scheduleEnsureActiveVisible)
    listObserver.observe(el.value, { childList: true })
})

onBeforeUnmount(() => {
    navEl?.removeEventListener('wheel', onWheel)
    listObserver?.disconnect()
    if (pendingFrame) cancelAnimationFrame(pendingFrame)
})
</script>

<template>
    <wa-tab-group ref="el" class="tab-bar" v-bind="$attrs"><slot /></wa-tab-group>
</template>

<style scoped>
.tab-bar {
    --track-width: var(--divider-size);
}

/* The compact size, carried once for every call site. Slotted <wa-tab>s come from
   the parent (they bear the parent's scope id, not ours), so reaching ::part requires
   :deep(). The direct-child combinator keeps the size from leaking into a nested tab
   bar rendered inside one of our panels (e.g. a TerminalPanel's own TabBar). */
.tab-bar > :deep(wa-tab::part(base)) {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    gap: var(--wa-space-2xs);
}
</style>
