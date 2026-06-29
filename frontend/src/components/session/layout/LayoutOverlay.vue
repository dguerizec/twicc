<script setup>
// A peek overlay (90% of the inner area) for an edge whose dock(s) couldn't fit as a column
// or are collapsed-to-overlay. Backdrop closes it; the gutter stays visible above. Its body
// is a Teleport target registered under 'overlay' (only the overlay-active panel targets it).
import { computed, ref, watchEffect } from 'vue'
import TabPlacementMenu from './TabPlacementMenu.vue'
import TabBar from '../../ui/TabBar.vue'

const props = defineProps({
    overlay: { type: Object, required: true }, // { edge, rect:{x,y,w,h}, tabs }
    activeTabId: { type: String, default: null },
    dockOf: { type: Function, required: true }, // tabId -> its current dockId | 'center'
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select', 'close', 'place'])

const bodyRef = ref(null)

const style = computed(() => ({
    left: `${props.overlay.rect.x}px`,
    top: `${props.overlay.rect.y}px`,
    width: `${props.overlay.rect.w}px`,
    height: `${props.overlay.rect.h}px`,
}))

watchEffect((onCleanup) => {
    const el = bodyRef.value
    if (!el) return
    props.registerTarget('overlay', el)
    onCleanup(() => props.unregisterTarget('overlay'))
})

function onShow(event) { emit('select', event.detail.name) }
</script>

<template>
    <div class="overlay-layer">
        <div class="overlay-backdrop" @click="emit('close')"></div>
        <div class="layout-overlay" :class="overlay.edge" :style="style" @click.stop>
            <TabBar class="overlay-tabnav" :active="activeTabId" @wa-tab-show.stop="onShow">
                <wa-tab v-for="t in overlay.tabs" :key="t.id" slot="nav" :panel="t.id" class="overlay-tab">
                    <wa-icon v-if="t.icon" :name="t.icon" class="overlay-tab-icon"></wa-icon>
                    <span>{{ t.label }}</span>
                    <TabPlacementMenu
                        :tab-id="t.id"
                        :current="dockOf(t.id)"
                        @place="(dest) => emit('place', t.id, dest)"
                    />
                </wa-tab>
                <wa-button
                    slot="nav"
                    class="overlay-close reduced-height"
                    appearance="plain"
                    size="small"
                    title="Close"
                    aria-label="Close overlay"
                    @click.stop="emit('close')"
                >
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </TabBar>
            <div ref="bodyRef" class="overlay-body"></div>
        </div>
    </div>
</template>

<style scoped>
.overlay-backdrop {
    position: absolute;
    inset: 0;
    z-index: 8;
    background: rgba(0, 0, 0, 0.2);
}
.layout-overlay {
    position: absolute;
    z-index: 11;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--wa-color-surface-default, #fff);
    box-shadow: var(--wa-shadow-l, 0 10px 40px rgba(0, 0, 0, 0.35));
    --overlay-border: var(--divider-size) solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
}
/* While a FilePane preview teleported into this overlay is expanded to full-window
   (position:fixed; z-index:1000), the overlay's own z-index:11 stacking context traps it
   below the gutters (z-index:12, resolved a context higher) — they paint over the fullscreen.
   Lift the overlay above the gutters so the fullscreen escapes them. Same :has() trigger as
   DockRegion's fix; there the region's stacking context comes from `isolation`, so it drops to
   auto, but the overlay's comes from an explicit z-index, so we raise that instead. */
.layout-overlay:has(.file-pane-preview--fullscreen) {
    z-index: 13;
}
/* The overlay peeks from one edge; only its inner edge (toward the escape strip / center) is bordered. */
.layout-overlay.left { border-right: var(--overlay-border); }
.layout-overlay.right { border-left: var(--overlay-border); }
.layout-overlay.bottom { border-top: var(--overlay-border); }
.overlay-tabnav {
    flex: 0 0 auto;
    min-width: 0;
    overflow: hidden;
}
.overlay-tabnav::part(body) {
    display: none;
}
.overlay-tabnav::part(tabs) {
    align-items: center;
}
.overlay-tab::part(base) {
    display: inline-flex;
    align-items: center;
}
.overlay-tab-icon {
    font-size: 0.85em;
}
.overlay-close {
    margin-inline-start: auto;
    --wa-form-control-padding-inline: 0.3em;
}
.overlay-body {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
</style>
