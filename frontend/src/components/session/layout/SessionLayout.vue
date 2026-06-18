<script setup>
// Dumb render of the dockable layout from the resolver's renderDescription. No layout math
// here. The CENTER is provided by the parent as the default slot (the existing wa-tab-group);
// this component only positions it and adds dock regions / gutters / overlay around it.
//
// Layout-only interactions (place, minimize, restore, gutter actions, overlay) are dispatched
// straight to the composable's actions. Tab selection is routed, so it bubbles up as
// 'select-tab' for the parent (which owns the route).
import { computed } from 'vue'
import DockRegion from './DockRegion.vue'
import DockGutter from './DockGutter.vue'
import LayoutOverlay from './LayoutOverlay.vue'

const props = defineProps({
    layout: { type: Object, required: true },       // the useSessionLayout() return
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select-tab', 'minimize', 'focus-pane', 'overlay-activate', 'overlay-dismiss'])

// props.layout is the useSessionLayout() return — a bag of refs/functions. Refs accessed
// through a prop object are NOT auto-unwrapped, so read them via .value here.
const docking = computed(() => props.layout.dockingRendered.value)
const render = computed(() => props.layout.render.value)
const openOverlayEdge = computed(() => props.layout.openOverlayEdge.value)

const centerRegion = computed(() => render.value.regions.find((r) => r.kind === 'center'))
const dockRegions = computed(() => render.value.regions.filter((r) => r.kind !== 'center'))

// Context classes on the root so descendant CSS can react to the mode and to what sits on the
// edges. Used to inset gutter icons away from the sidebar-reopen toggle when closed, and to
// modulate the chat composer's toggle-clearance padding: a bottom dock/gutter lifts the composer
// above the toggle, a left column pushes it clear, a left gutter clears it only partly.
const rootClasses = computed(() => {
    const d = render.value
    return {
        [`mode-${d.mode}`]: true,
        'has-left-gutter': d.gutters.some((g) => g.edge === 'left'),
        'has-left-col': d.regions.some((r) => r.kind === 'col-left'),
        'has-bottom-gutter': d.gutters.some((g) => g.edge === 'bottom'),
        'has-bottom-region': d.regions.some((r) => r.kind === 'bottom'),
    }
})

const centerStyle = computed(() => {
    if (!docking.value || !centerRegion.value) return {}
    const r = centerRegion.value
    return { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` }
})

const overlay = computed(() =>
    render.value.overlays.find((o) => o.edge === openOverlayEdge.value) || null
)
// The overlay shows the active (route) tab — it's open precisely because that tab is in overlay
// mode (see useSessionLayout: openOverlayEdge is derived from the route).
const overlayActive = computed(() => overlay.value ? props.layout.routeActiveTabId.value : null)

function onGutterAction({ edge, dockId, tabId, action }) {
    if (action === 'swap') {
        props.layout.swapSide(edge)
        emit('select-tab', tabId)
    } else if (action === 'restore') {
        props.layout.restore(dockId)
        emit('select-tab', tabId)
    } else {
        // The overlay is derived from the route: opening it = navigating to the tab ('activate');
        // re-clicking the tab already shown = dismissing it (navigate back to the prior tab).
        if (props.layout.openOverlayEdge.value === edge && props.layout.routeActiveTabId.value === tabId) {
            emit('overlay-dismiss')
        } else {
            emit('overlay-activate', tabId)
        }
    }
}
function onOverlaySelect(tabId) {
    if (tabId === overlayActive.value) return
    emit('overlay-activate', tabId)
}
// Explicit close (backdrop / close button): dismiss navigates back to the pre-open active tab,
// which drops the route out of overlay mode and closes the overlay.
function onOverlayClose() {
    emit('overlay-dismiss')
}
</script>

<template>
    <div class="session-layout" :class="rootClasses">
        <div class="center-slot" :style="centerStyle">
            <slot></slot>
        </div>

        <template v-if="docking">
            <DockRegion
                v-for="r in dockRegions"
                :key="r.id"
                :region="r"
                :active-tab-id="layout.regionActiveTabId(r)"
                :register-target="registerTarget"
                :unregister-target="unregisterTarget"
                @select="(id) => emit('select-tab', id)"
                @pane-focus="(id) => emit('focus-pane', id)"
                @minimize="(dockIds) => emit('minimize', dockIds)"
                @place="(id, dest) => layout.place(id, dest)"
            />

            <DockGutter
                v-for="g in render.gutters"
                :key="g.edge"
                :gutter="g"
                :open-overlay-edge="openOverlayEdge"
                @action="onGutterAction"
            />

            <LayoutOverlay
                v-if="overlay"
                :overlay="overlay"
                :active-tab-id="overlayActive"
                :dock-of="layout.dockOf"
                :register-target="registerTarget"
                :unregister-target="unregisterTarget"
                @select="onOverlaySelect"
                @close="onOverlayClose"
                @place="(id, dest) => layout.place(id, dest)"
            />
        </template>
    </div>
</template>

<style scoped>
.session-layout {
    position: relative;
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
}
.center-slot {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
}

/* When the session-list sidebar is closed, its reopen toggle floats at the bottom-left of the
   content area; nearby UI must clear it. The base clearance values live once on body.sidebar-closed
   (see App.vue); here the session view only *refines* --sidebar-toggle-clearance-x for what sits at
   the bottom-left — a thin left rail → partial; a bottom dock/gutter or a left column already clears
   it → none. Nothing docked falls through to the body base (full). The composer and left gutter
   below consume the value. This is the single place the "which dock context needs how much" lives. */
body.sidebar-closed .session-layout.has-left-gutter:not(.has-left-col):not(.has-bottom-region):not(.has-bottom-gutter) {
    --sidebar-toggle-clearance-x: 1.5rem;
}
body.sidebar-closed .session-layout:is(.has-bottom-region, .has-bottom-gutter, .has-left-col) {
    --sidebar-toggle-clearance-x: 0rem;
}

/* The bottom gutter's own start icons sit slightly closer to the toggle than the composer, so they
   keep their own inset (not the shared -x) to stay pixel-stable. */
body.sidebar-closed .session-layout.mode-widescreen:not(.has-left-gutter):not(.has-left-col) :deep(.dock-gutter.bottom .g-group.start) {
    padding-inline-start: 3rem;
}
body.sidebar-closed .session-layout.mode-widescreen.has-left-gutter :deep(.dock-gutter.bottom .g-group.start) {
    padding-inline-start: 1.5rem;
}
body.sidebar-closed .session-layout :deep(.dock-gutter.left .g-group.end) {
    padding-block-end: var(--sidebar-toggle-clearance-y);
}
</style>
