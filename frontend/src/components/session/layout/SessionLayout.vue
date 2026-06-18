<script setup>
// Dumb render of the dockable layout from the resolver's renderDescription. No layout math
// here. The CENTER is provided by the parent as the default slot (the existing wa-tab-group);
// this component only positions it and adds dock regions / gutters / overlay around it.
//
// Layout-only interactions (place, minimize, restore, gutter actions, overlay) are dispatched
// straight to the composable's actions. Tab selection is routed, so it bubbles up as
// 'select-tab' for the parent (which owns the route).
import { computed, ref, onBeforeUnmount } from 'vue'
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

// ---- Resize splitters. The resolver emits every draggable boundary with its geometry + math params
// (axis / origin / extent / from / configKey): kind 'dock' (a side column's width or the bottom's
// height vs the center) and kind 'sib' (between two siblings of a split column, or the two bottom
// siblings). One generic handler covers both — we draw an invisible hit strip over each boundary and
// write the dragged fraction to the store, which the resolver re-clamps on the next render (px mins
// for docks, siblingMaxFrac for sibs). The fraction is part of the (ephemeral) layout intention —
// persistence is deferred. Window-level listeners so the drag survives the re-render that repositions
// the handle. ----
const sessionLayoutEl = ref(null)
const resizeSplitters = computed(() => render.value.splitters || [])
const draggingId = ref(null)

const HANDLE = 9 // px hit-area thickness, centered on the divider line
function splitterStyle(s) {
    if (s.axis === 'v') {
        return { left: `${s.x - HANDLE / 2}px`, top: `${s.y}px`, width: `${HANDLE}px`, height: `${s.h}px` }
    }
    return { left: `${s.x}px`, top: `${s.y - HANDLE / 2}px`, width: `${s.w}px`, height: `${HANDLE}px` }
}

let drag = null
function onSplitterMove(event) {
    if (!drag || !sessionLayoutEl.value) return
    const rect = sessionLayoutEl.value.getBoundingClientRect()
    const pointer = drag.axis === 'h' ? event.clientY - rect.top : event.clientX - rect.left
    const frac = (drag.from === 'end' ? drag.origin - pointer : pointer - drag.origin) / drag.extent
    props.layout.setResizeFraction(drag.configKey, frac)
}
function endDrag() {
    drag = null
    draggingId.value = null
    window.removeEventListener('pointermove', onSplitterMove)
    window.removeEventListener('pointerup', endDrag)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
}
function onSplitterDown(event, s) {
    event.preventDefault()
    // Only the side-column docks set activeResize (the dragged side wins the squeeze when both show);
    // sibling and bottom-height splitters leave it untouched.
    if (s.configKey === 'leftColFrac') props.layout.setActiveResize('left')
    else if (s.configKey === 'rightColFrac') props.layout.setActiveResize('right')
    drag = { axis: s.axis, origin: s.axis === 'h' ? s.originY : s.originX, from: s.from || 'start', extent: s.extent, configKey: s.configKey }
    draggingId.value = s.id
    window.addEventListener('pointermove', onSplitterMove)
    window.addEventListener('pointerup', endDrag)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = s.axis === 'v' ? 'col-resize' : 'row-resize'
}
onBeforeUnmount(endDrag)
</script>

<template>
    <div ref="sessionLayoutEl" class="session-layout" :class="rootClasses">
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

            <div
                v-for="s in resizeSplitters"
                :key="s.id"
                class="layout-splitter"
                :class="[`axis-${s.axis}`, `kind-${s.kind}`, { dragging: draggingId === s.id }]"
                :style="splitterStyle(s)"
                @pointerdown="onSplitterDown($event, s)"
            >
                <wa-icon name="grip-lines-vertical" class="splitter-grip"></wa-icon>
            </div>

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

/* Dock resize handles: an invisible hit strip over a column/center or bottom/center boundary. The
   divider line itself is the adjacent region's border; the ::after is only the grab affordance,
   revealed on hover and during the drag. Below the gutters (12) and overlay (11) so those win. */
.layout-splitter {
    position: absolute;
    z-index: 5;
    touch-action: none;
    display: flex;
    align-items: center;
    justify-content: center;
}
.layout-splitter.axis-v { cursor: col-resize; }
.layout-splitter.axis-h { cursor: row-resize; }
/* Touch affordance: a persistent grip on the divider, mirroring the sidebar splitter's
   .divider-handle — shown only on coarse-pointer (touch) devices where there's no hover to reveal
   the line. Scaled up so it overflows the thin 9px strip: a pointerdown on the grip bubbles to the
   strip and starts the drag, so the enlarged grip IS a much larger tap surface to grab (the whole
   point on touch). Rotated 90° on the horizontal (axis-h) splitters so the grip lines run along the
   divider. */
.splitter-grip {
    display: none;
    color: var(--wa-color-surface-border);
    scale: 3;
}
.layout-splitter.axis-h .splitter-grip {
    rotate: 90deg;
}
@media (pointer: coarse) {
    .splitter-grip {
        display: inline;
    }
}
.layout-splitter::after {
    content: '';
    position: absolute;
    background: var(--wa-color-brand-fill-loud);
    opacity: 0;
    transition: opacity 0.12s ease;
}
.layout-splitter.axis-v::after { top: 0; bottom: 0; left: 50%; transform: translateX(-50%); width: 2px; }
.layout-splitter.axis-h::after { left: 0; right: 0; top: 50%; transform: translateY(-50%); height: 2px; }
.layout-splitter:hover::after { opacity: 0.5; }
.layout-splitter.dragging::after { opacity: 1; }
</style>
