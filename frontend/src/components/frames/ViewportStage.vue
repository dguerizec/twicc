<script setup>
// Scrollable body + hatched canvas + fixed-size stage of the responsive-
// viewport mode, shared by the Browser pane and the artifact HTML preview.
// The default slot carries the pane's PersistentFrame; the stage box is the
// frame placeholder's sizing parent. In normal mode every wrapper is a plain
// flex pass-through; in responsive mode the stage takes the exact CSS-pixel
// width/height and the body scrolls around it. The wrappers render in BOTH
// modes (restyled only) so the slotted frame never unmounts on a toggle — a
// remount would re-register the pooled iframe and reload it.
//
// Owners bind v-model:width/v-model:height and read `bodyEl` (exposed) as the
// frame's clip container (PersistentFrame's clip-el prop).
import { computed, onBeforeUnmount, ref } from 'vue'
import { useFramePoolStore } from '../../stores/framePool'
import { clampViewportSize } from './viewport'

// Resize handles around the stage — one per side and corner. dirX/dirY say
// which way each handle pushes a dimension: e.g. the west handle (dirX -1)
// grows the width when dragged left. 0 = that axis is untouched.
const VIEWPORT_HANDLES = [
    { key: 'n', dirX: 0, dirY: -1 },
    { key: 's', dirX: 0, dirY: 1 },
    { key: 'e', dirX: 1, dirY: 0 },
    { key: 'w', dirX: -1, dirY: 0 },
    { key: 'ne', dirX: 1, dirY: -1 },
    { key: 'nw', dirX: -1, dirY: -1 },
    { key: 'se', dirX: 1, dirY: 1 },
    { key: 'sw', dirX: -1, dirY: 1 },
]

const props = defineProps({
    // Responsive mode on/off (off = transparent flex pass-through).
    active: { type: Boolean, default: false },
    width: { type: Number, required: true },
    height: { type: Number, required: true },
    // Whether the canvas/stage (default slot) renders at all; when false the
    // `empty` slot renders instead (e.g. the Browser pane's no-URL state).
    showStage: { type: Boolean, default: true },
})
const emit = defineEmits(['update:width', 'update:height'])

const framePool = useFramePoolStore()
const bodyEl = ref(null)

const stageStyle = computed(() =>
    props.active ? { width: `${props.width}px`, height: `${props.height}px` } : null
)

// Drag-resize via the handles in the hatched gutter. Pointer capture keeps
// the events flowing to the handle; beginDividerDrag() additionally turns off
// pointer-events on ALL pooled iframes (same need as split dividers: an
// iframe is a separate browsing context that swallows move events the moment
// the pointer crosses it). Incremental (per-move) deltas, so the doubling
// below can flip mid-drag as an axis crosses the fit/overflow boundary. The
// drag tracks its own width/height copy — reading props back would race the
// parent's async re-render between two pointermove events.
let drag = null // { dirX, dirY, lastX, lastY, width, height }
const dragKey = ref(null) // template mirror — keeps the handle lit

function startResize(handle, event) {
    if (drag) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag = {
        dirX: handle.dirX,
        dirY: handle.dirY,
        lastX: event.clientX,
        lastY: event.clientY,
        width: props.width,
        height: props.height,
    }
    dragKey.value = handle.key
    framePool.beginDividerDrag()
}

function onResizeMove(event) {
    if (!drag) return
    const body = bodyEl.value
    const dx = event.clientX - drag.lastX
    const dy = event.clientY - drag.lastY
    drag.lastX = event.clientX
    drag.lastY = event.clientY
    // While an axis has no overflow the stage is centered on it (margin:auto),
    // so its dragged edge moves only half as fast as the box grows — double
    // the delta to keep the handle under the pointer. Once the axis overflows
    // the stage anchors to the start and it's back to 1:1. `+ 1` absorbs
    // sub-pixel rounding on the scroll/client comparison. dirX/dirY carry the
    // handle's sign, so a west/north handle grows the box when dragged out.
    if (drag.dirX) {
        const factor = body && body.scrollWidth <= body.clientWidth + 1 ? 2 : 1
        drag.width = clampViewportSize(drag.width + drag.dirX * dx * factor)
        emit('update:width', drag.width)
    }
    if (drag.dirY) {
        const factor = body && body.scrollHeight <= body.clientHeight + 1 ? 2 : 1
        drag.height = clampViewportSize(drag.height + drag.dirY * dy * factor)
        emit('update:height', drag.height)
    }
}

function endResize() {
    if (!drag) return
    drag = null
    dragKey.value = null
    framePool.endDividerDrag()
}

// Balance the pool's divider-drag depth if unmounted mid-drag.
onBeforeUnmount(endResize)

defineExpose({ bodyEl })
</script>

<template>
    <div ref="bodyEl" class="viewport-body" :class="{ 'viewport-body--responsive': active }">
        <div v-if="showStage" class="viewport-canvas">
            <div class="viewport-stage" :style="stageStyle">
                <slot />
                <!-- Resize handles live in the hatched gutter just OUTSIDE the
                     stage box, so the pooled iframe (which overlays exactly
                     the stage rect) never covers them. -->
                <template v-if="active">
                    <div
                        v-for="handle in VIEWPORT_HANDLES"
                        :key="handle.key"
                        class="viewport-handle"
                        :class="[
                            `viewport-handle--${handle.key}`,
                            { 'viewport-handle--dragging': dragKey === handle.key },
                        ]"
                        @pointerdown="startResize(handle, $event)"
                        @pointermove="onResizeMove"
                        @pointerup="endResize"
                        @pointercancel="endResize"
                    ></div>
                </template>
            </div>
        </div>
        <slot v-else name="empty" />
    </div>
</template>

<style scoped>
.viewport-body {
    flex: 1;
    min-height: 0;
    display: flex;
}

/* Responsive mode: the body turns into a scroll container around the
   fixed-size stage. */
.viewport-body--responsive {
    display: block;
    overflow: auto;
}

/* Pass-through wrapper in normal mode; in responsive mode it carries the
   hatched "neutral zone" background and the gutter around the stage. */
.viewport-canvas {
    flex: 1;
    display: flex;
    min-width: 0;
    min-height: 0;
}

/* width/height: max-content on purpose: unlike the scroll container's own
   padding, a child's box is part of the scrollable content on EVERY side, so
   the gutter (and the resize handles living in it) stays reachable even when
   the stage overflows the pane. min-*: 100% makes the canvas at least fill the
   pane, giving margin:auto (below) room to center the stage. The +12px in the
   padding hosts the resize handles, which sit just outside the stage box. */
.viewport-body--responsive .viewport-canvas {
    display: flex;
    width: max-content;
    height: max-content;
    min-width: 100%;
    min-height: 100%;
    padding: calc((var(--wa-space-m) + 12px) * 2);
    background-color: var(--wa-color-surface-lowered);
    background-image: repeating-linear-gradient(
        45deg,
        transparent 0,
        transparent 6px,
        color-mix(in srgb, var(--wa-color-neutral-fill-loud) 12%, transparent) 6px,
        color-mix(in srgb, var(--wa-color-neutral-fill-loud) 12%, transparent) 7px
    );
}

/* Normal mode: the stage just relays the flex sizing down to the frame. */
.viewport-stage {
    flex: 1;
    display: flex;
    min-width: 0;
    min-height: 0;
}

/* Responsive mode: the stage IS the device viewport — an exact-CSS-pixel box
   (inline style) the placeholder fills. margin:auto centers it both axes when
   it fits; per the flexbox spec, auto margins resolve to 0 on overflow, so it
   then anchors to the start (top-left) and the whole thing stays scroll-
   reachable — NOT the unreachable-start trap of justify/align: center. */
.viewport-body--responsive .viewport-stage {
    position: relative;
    flex: none;
    display: block;
    margin: auto;
}

/* Drag handles in the gutter just outside the stage box (see template note).
   Sides span the corresponding edge; corners fill the 12px squares the sides
   leave uncovered (sides run 0→edge, corners sit at -12px), so the perimeter
   is seamless with no overlap. */
.viewport-handle {
    position: absolute;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: none;
}

.viewport-handle::after {
    content: '';
    border-radius: 999px;
    background: var(--wa-color-neutral-fill-loud);
    opacity: 0.5;
    transition: opacity 0.15s ease;
}

.viewport-handle:hover::after,
.viewport-handle--dragging::after {
    opacity: 1;
}

/* Vertical sides (west/east): full-height strips, a vertical grip bar. */
.viewport-handle--e,
.viewport-handle--w {
    top: 0;
    bottom: 0;
    width: 12px;
    cursor: ew-resize;
}

.viewport-handle--e {
    right: -12px;
}

.viewport-handle--w {
    left: -12px;
}

.viewport-handle--e::after,
.viewport-handle--w::after {
    width: 4px;
    height: 2.5rem;
    max-height: 60%;
}

/* Horizontal sides (north/south): full-width strips, a horizontal grip bar. */
.viewport-handle--n,
.viewport-handle--s {
    left: 0;
    right: 0;
    height: 12px;
    cursor: ns-resize;
}

.viewport-handle--s {
    bottom: -12px;
}

.viewport-handle--n {
    top: -12px;
}

.viewport-handle--n::after,
.viewport-handle--s::after {
    height: 4px;
    width: 2.5rem;
    max-width: 60%;
}

/* Corners: 12px squares with a small square grip; cursor matches the diagonal
   (nwse for the ↖↘ pair, nesw for the ↗↙ pair). */
.viewport-handle--ne,
.viewport-handle--nw,
.viewport-handle--se,
.viewport-handle--sw {
    width: 12px;
    height: 12px;
}

.viewport-handle--ne::after,
.viewport-handle--nw::after,
.viewport-handle--se::after,
.viewport-handle--sw::after {
    width: 8px;
    height: 8px;
    border-radius: var(--wa-border-radius-s);
}

.viewport-handle--nw {
    top: -12px;
    left: -12px;
    cursor: nwse-resize;
}

.viewport-handle--se {
    bottom: -12px;
    right: -12px;
    cursor: nwse-resize;
}

.viewport-handle--ne {
    top: -12px;
    right: -12px;
    cursor: nesw-resize;
}

.viewport-handle--sw {
    bottom: -12px;
    left: -12px;
    cursor: nesw-resize;
}
</style>
