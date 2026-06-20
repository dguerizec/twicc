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
const emit = defineEmits(['select-tab', 'tab-activate', 'minimize', 'maximize', 'restore-maximized', 'focus-pane', 'overlay-activate', 'overlay-dismiss'])

// props.layout is the useSessionLayout() return — a bag of refs/functions. Refs accessed
// through a prop object are NOT auto-unwrapped, so read them via .value here.
const docking = computed(() => props.layout.dockingRendered.value)
const render = computed(() => props.layout.render.value)
const openOverlayEdge = computed(() => props.layout.openOverlayEdge.value)

// The route owner — the one tab whose content the URL points at, across all regions. Each DockRegion
// compares it to its own shown tab: the region that owns it keeps a full-opacity tab bar, the others
// dim theirs, so the active region stands out among the several that can show content at once.
const focusedTabId = computed(() => props.layout.routeActiveTabId.value)

const centerRegion = computed(() => render.value.regions.find((r) => r.kind === 'center'))
// Normal dock regions only — excludes the synthetic 'maximized' region, which is handled apart.
const dockRegions = computed(() => render.value.regions.filter((r) => r.kind !== 'center' && r.kind !== 'maximized'))

// Maximized mode: the resolver returns a single region of kind 'maximized'. A maximized DOCK renders
// as that one full-bleed DockRegion (center + everything else hidden); a maximized CENTER instead just
// fills the center slot (the center is the default slot — no teleport), hiding all docks.
const maximizedRegion = computed(() => render.value.regions.find((r) => r.kind === 'maximized') || null)
const isCenterMaximized = computed(() => !!maximizedRegion.value?.slots.some((s) => s.dockId === 'center'))
const maximizedDockRegion = computed(() => (maximizedRegion.value && !isCenterMaximized.value) ? maximizedRegion.value : null)
const centerVisible = computed(() => !maximizedDockRegion.value)

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
    // When the center is maximized it fills the whole area (the resolver's region rect is the viewport).
    const r = (isCenterMaximized.value && maximizedRegion.value) || (docking.value && centerRegion.value)
    if (!r) return {}
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

function splitterStyle(s) {
    // Only the resolver position + the long dimension. The thin dimension and the on-divider
    // centering live in CSS (--resize-grab + translate -50%), so there's no px magic here and it
    // adapts to the theme's divider thickness.
    return s.axis === 'v'
        ? { left: `${s.x}px`, top: `${s.y}px`, height: `${s.h}px` }
        : { left: `${s.x}px`, top: `${s.y}px`, width: `${s.w}px` }
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
        <div class="center-slot" :style="centerStyle" v-show="centerVisible">
            <slot></slot>
        </div>

        <!-- A maximized DOCK: the single full-bleed region with a restore button; nothing else. -->
        <DockRegion
            v-if="maximizedDockRegion"
            :region="maximizedDockRegion"
            :active-tab-id="layout.regionActiveTabId(maximizedDockRegion)"
            :focused-tab-id="focusedTabId"
            :maximized="true"
            :register-target="registerTarget"
            :unregister-target="unregisterTarget"
            @select="(id) => emit('select-tab', id)"
            @tab-activate="(id) => emit('tab-activate', id)"
            @pane-focus="(id) => emit('focus-pane', id)"
            @restore="emit('restore-maximized')"
            @place="(id, dest) => layout.place(id, dest)"
        />

        <!-- Normal dockable layout — skipped while maximizing (a maximized center shows only the
             center slot above; a maximized dock shows only the region above). -->
        <template v-else-if="docking && !maximizedRegion">
            <DockRegion
                v-for="r in dockRegions"
                :key="r.id"
                :region="r"
                :active-tab-id="layout.regionActiveTabId(r)"
                :focused-tab-id="focusedTabId"
                :register-target="registerTarget"
                :unregister-target="unregisterTarget"
                @select="(id) => emit('select-tab', id)"
                @tab-activate="(id) => emit('tab-activate', id)"
                @pane-focus="(id) => emit('focus-pane', id)"
                @minimize="(dockIds) => emit('minimize', dockIds)"
                @maximize="(dockIds, tab) => emit('maximize', dockIds, tab)"
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
                <wa-icon name="grip-lines-vertical" auto-width class="splitter-grip"></wa-icon>
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
   content area; nearby UI must clear it. The base values live once on body.sidebar-closed (App.vue);
   here we refine per dock context. --left-x is the left-edge-only clearance (thin left rail →
   reduced, full left column → none). The composer's -x follows --left-x, but a bottom dock *region*
   zeroes it (the composer is lifted above the toggle); a bottom *gutter* does not (it's thin — the
   composer still overlaps the toggle). The composer, the left gutter and the terminal extra-keys bar
   below consume these — the single place "which dock context needs how much" lives. */
body.sidebar-closed .session-layout.has-left-gutter:not(.has-left-col) {
    --sidebar-toggle-clearance-left-x: 1rem;
}
body.sidebar-closed .session-layout.has-left-col {
    --sidebar-toggle-clearance-left-x: 0rem;
}
body.sidebar-closed .session-layout {
    --sidebar-toggle-clearance-x: var(--sidebar-toggle-clearance-left-x);
    /* Terminal extra-keys bar: no clearance by default (overridden below where it sits bottom-left). */
    --sidebar-toggle-clearance-extra-keys: var(--wa-space-xs);
}
body.sidebar-closed .session-layout.has-bottom-region {
    --sidebar-toggle-clearance-x: 0rem;
}

/* The terminal's extra-keys bar sits where the composer would but is taller, so it clears the toggle
   by --left-x + 1rem — and only when it's actually at the bottom-left: the terminal is the sole or
   bottom-left bottom dock (never bottom-right), or it's in the center and the center reaches the
   bottom-left (no left column, no bottom dock lifting it), or it's maximized full-bleed. The value is
   set on the nearest layout element (the bottom dock region / the center slot / the maximized region);
   it inherits across the panel teleport into the bar, so no :deep into the relocated terminal needed. */
body.sidebar-closed .session-layout:not(.has-left-col) .dock-region[data-rid="bottom"],
body.sidebar-closed .session-layout:not(.has-left-col) .dock-region[data-rid="bottom-left"],
body.sidebar-closed .session-layout .dock-region[data-rid="maximized"] {
    --sidebar-toggle-clearance-extra-keys: calc(var(--sidebar-toggle-clearance-left-x) + 1rem);
}
body.sidebar-closed .session-layout:not(.has-left-col):not(.has-bottom-region) .center-slot {
    --sidebar-toggle-clearance-extra-keys: calc(var(--sidebar-toggle-clearance-left-x) + 1rem);
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
    /* Grid so the grip child is centered with place-content (no px offsets, no transform tricks). */
    display: grid;
    place-content: center;
    /* The only sizing knob: the grab-strip thickness. The visible line stays var(--divider-size),
       which varies with the theme; the strip is centered on its divider with translate, so
       everything stays aligned whatever the divider thickness is. */
    --resize-grab: 0.6rem;
}
/* Center each strip on its divider with translate (not a px offset); thin dimension from the token. */
.layout-splitter.axis-v { width: var(--resize-grab); translate: -50% 0; cursor: col-resize; }
.layout-splitter.axis-h { height: var(--resize-grab); translate: 0 -50%; cursor: row-resize; }

/* Hover/drag highlight: a line the thickness of the theme's divider, centered in the strip. */
.layout-splitter::after {
    content: '';
    position: absolute;
    background: var(--wa-color-brand-fill-loud);
    opacity: 0;
    transition: opacity 0.12s ease;
}
.layout-splitter.axis-v::after { top: 0; bottom: 0; left: 50%; width: var(--divider-size); translate: -50% 0; }
.layout-splitter.axis-h::after { left: 0; right: 0; top: 50%; height: var(--divider-size); translate: 0 -50%; }
.layout-splitter:hover::after { opacity: 0.5; }
.layout-splitter.dragging::after { opacity: 1; }

/* Touch affordance: a persistent grip centered on the strip (by the strip's grid place-content),
   mirroring the sidebar splitter's .divider-handle — shown only on coarse-pointer devices where
   there's no hover. Scaled up so it overflows the thin strip: a pointerdown on it bubbles to the
   strip and starts the drag, so the grip is a large tap surface. Rotated 90° on the horizontal
   (axis-h) splitters so the grip lines run along the divider. */
.splitter-grip {
    display: none;
    scale: 3;
    color: var(--wa-color-surface-border);
}
.layout-splitter.axis-h .splitter-grip { rotate: 90deg; }
@media (pointer: coarse) {
    .splitter-grip { display: block; }
}
</style>
