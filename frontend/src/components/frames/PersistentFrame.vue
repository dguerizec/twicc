<script setup>
// Placeholder side of the persistent-frame system (see stores/framePool.js).
// Renders an empty div where the iframe visually belongs; the actual <iframe>
// is rendered by FrameHost, positioned over this placeholder's live rect, and
// NEVER moves in the DOM — so KeepAlive session switches and dock Teleports
// stop reloading it. Falls back to a plain inline iframe when no host is
// mounted (contexts outside ProjectView).
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useElementBounding } from '@vueuse/core'
import { useFramePoolStore } from '../../stores/framePool'

const props = defineProps({
    // Unique, stable identity (derive from useId() in the owner). Two cached
    // sessions must NOT share an id.
    frameId: { type: String, required: true },
    src: { type: String, required: true },
    // Changing it re-creates the iframe element in place (intentional reload).
    remountKey: { type: [String, Number], default: 0 },
    // Extra iframe attributes (sandbox, allow, title, …), applied via v-bind.
    attrs: { type: Object, default: () => ({}) },
    // Owner-provided stacking situation (see plan §2 z-tiers).
    elevated: { type: Boolean, default: false },   // inside the docking overlay
    fullscreen: { type: Boolean, default: false }, // preview expanded full-window
})

const emit = defineEmits(['load'])

const pool = useFramePoolStore()
const placeholderEl = ref(null)
const inlineFrameEl = ref(null)
const inlineOverlayEl = ref(null)

// Pooled unless no host exists — decided at mount time (a host appearing later
// would re-home the iframe, i.e. reload it; not worth handling). ProjectView
// sets hostMounted in its OWN setup, which runs before any of its children —
// so this snapshot is reliable even on cold-load deep links where panes mount
// before FrameHost's DOM does.
const pooled = pool.hostMounted

const zTier = computed(() => (props.fullscreen ? 'fullscreen' : props.elevated ? 'overlay' : 'base'))

// KeepAlive detach is invisible to ResizeObserver — the hooks are the
// authoritative signal.
const activated = ref(true)
onActivated(() => {
    activated.value = true
    bounding.update()
})
onDeactivated(() => {
    activated.value = false
})

const bounding = useElementBounding(placeholderEl)
const visible = computed(
    () => activated.value && bounding.width.value > 0.5 && bounding.height.value > 0.5
)

if (pooled) {
    pool.register(props.frameId, {
        src: props.src,
        remountKey: props.remountKey,
        attrs: props.attrs,
        zTier: zTier.value,
        onLoad: (event) => emit('load', event),
    })
    onBeforeUnmount(() => pool.unregister(props.frameId))

    watch([() => props.src, () => props.remountKey, () => props.attrs, zTier], () => {
        pool.patch(props.frameId, {
            src: props.src,
            remountKey: props.remountKey,
            attrs: props.attrs,
            zTier: zTier.value,
        })
    })
    watch(visible, (v) => pool.patch(props.frameId, { visible: v }), { immediate: true })
    watch(
        [bounding.x, bounding.y, bounding.width, bounding.height],
        ([x, y, width, height]) => pool.setRect(props.frameId, { x, y, width, height })
    )
    // Layout mutations that move without resizing (dock retarget, overlay
    // open/close, maximize) are announced through the epoch.
    watch(() => pool.geometryEpoch, () => bounding.update(), { flush: 'post' })
}

onMounted(() => bounding.update())

// The live iframe element (pooled or inline) — owners use it for
// contentWindow (companion postMessage) and load listeners (broker).
const frameEl = computed(() =>
    pooled ? pool.frameEl(props.frameId) : inlineFrameEl.value
)
// Target for the owner's over-iframe chrome (Teleport :to) — sits above the
// iframe in the host cell (or locally in inline mode).
const overlayEl = computed(() =>
    pooled ? pool.frameOverlayEl(props.frameId) : inlineOverlayEl.value
)

defineExpose({ frameEl, overlayEl })
</script>

<template>
    <div ref="placeholderEl" class="persistent-frame-placeholder">
        <template v-if="!pooled">
            <iframe
                ref="inlineFrameEl"
                :key="remountKey"
                :src="src"
                v-bind="attrs"
                class="persistent-frame-inline"
                @load="emit('load', $event)"
            ></iframe>
            <div ref="inlineOverlayEl" class="persistent-frame-inline-overlay"></div>
        </template>
    </div>
</template>

<style scoped>
.persistent-frame-placeholder {
    position: relative;
    overflow: hidden;
}

.persistent-frame-inline {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
    background: #fff;
}

/* Same opt-in pointer-events contract as FrameHost's .frame-overlay-layer:
   the layer is inert, each teleported piece re-enables itself. */
.persistent-frame-inline-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
}
</style>
