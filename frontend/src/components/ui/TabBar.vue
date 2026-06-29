<script setup>
/**
 * TabBar - Unified wrapper around <wa-tab-group>.
 *
 * A transparent, pre-styled tab group: write the exact same `<wa-tab slot="nav">`
 * and `<wa-tab-panel>` children you would put in a raw <wa-tab-group>, and they
 * pass straight through. The wrapper only carries our shared defaults so the
 * compact size no longer has to be re-declared at every call site.
 *
 * Behaviour added on top: when the tabs overflow (WA shows its scroll chevrons),
 * a vertical mouse wheel over the tab strip scrolls it horizontally. Touch panning
 * and horizontal trackpad scrolling already work via the native overflow-x.
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

// Wheel-to-horizontal scroll, scoped to WA's internal `.nav` scroller (the same
// element its chevrons drive) so wheeling over a tab panel is never hijacked. Only
// a vertical wheel is translated, and only when the strip overflows — otherwise the
// page scrolls as usual. Horizontal wheel (deltaX / trackpad) and touch panning are
// left to the native overflow-x. `.nav` is a WA internal, so we no-op if it's gone.
let navEl = null

function onWheel(event) {
    if (event.deltaY === 0 || event.shiftKey) return
    if (!navEl || navEl.scrollWidth <= navEl.clientWidth) return
    navEl.scrollLeft += event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY
    event.preventDefault()
}

onMounted(async () => {
    if (el.value?.updateComplete) await el.value.updateComplete
    navEl = el.value?.shadowRoot?.querySelector('.nav')
    navEl?.addEventListener('wheel', onWheel, { passive: false })
})

onBeforeUnmount(() => {
    navEl?.removeEventListener('wheel', onWheel)
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
