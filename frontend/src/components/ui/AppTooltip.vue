<script setup>
/**
 * AppTooltip - Unified tooltip wrapper around wa-tooltip.
 *
 * Automatically hides tooltips on touch devices (where hover is not available).
 * On non-touch devices, tooltips are always shown.
 *
 * Usage:
 *   <AppTooltip :for="elementId">Tooltip text</AppTooltip>
 *
 * Props:
 *   - force: When true, the tooltip is always shown even on touch devices.
 *     Use for critical UI elements like quota indicators where the tooltip
 *     provides essential information.
 *   - interactive: When true, the tooltip holds controls the pointer must be
 *     able to reach (buttons, links). Adds a grace period before it closes,
 *     cancelled as soon as the pointer lands on it. See cancelPendingHide.
 *
 * All extra attributes are forwarded to the underlying <wa-tooltip>.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'

/**
 * Grace period, in ms, before an interactive tooltip closes once the pointer
 * has left it. Long enough to cross the gap wa-tooltip leaves between the
 * anchor and the tooltip body (its `distance`, 8px by default) whatever the
 * placement and however diagonal the move, short enough to still feel
 * immediate when moving away for good.
 */
const INTERACTIVE_HIDE_DELAY = 300

/**
 * Open interactive tooltips, which are mutually exclusive. The grace period
 * would otherwise keep one on screen while the next one opens (its `showDelay`
 * is shorter), and the two would overlap — anchors are usually stacked close
 * together, their tooltips are much taller than the anchors themselves.
 */
const openInteractiveTooltips = new Set()

const props = defineProps({
    force: {
        type: Boolean,
        default: false,
    },
    interactive: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()
const shouldShow = computed(() => props.force || !settingsStore.isTouchDevice)

const tooltipEl = ref(null)

/**
 * wa-tooltip never cancels a pending hide when the pointer enters the tooltip:
 * it binds `mouseover` on the anchor only, and its own `mouseout` handler
 * returns *before* clearing the timer when the tooltip is hovered. So the hide
 * scheduled while the pointer crosses the anchor-to-tooltip gap still fires,
 * closing the tooltip under the pointer. That is invisible with the default 0ms
 * delay, but it defeats the grace period interactive tooltips need — so we
 * close that gap ourselves.
 *
 * `hoverTimeout` is wa-tooltip's single show/hide timer handle (a plain
 * property, not a private field). Clearing it here can never swallow a pending
 * *show*: a closed tooltip has no hit area, so it emits no mouseover.
 */
function cancelPendingHide() {
    clearPendingTimer(tooltipEl.value)
}

function clearPendingTimer(el) {
    if (typeof el?.hoverTimeout === 'number') {
        clearTimeout(el.hoverTimeout)
    }
}

function handleShow(event) {
    // wa-show bubbles and is composed: ignore the ones fired by nested wa-*.
    if (event.target !== tooltipEl.value) {
        return
    }
    for (const other of [...openInteractiveTooltips]) {
        if (other !== tooltipEl.value) {
            clearPendingTimer(other)
            other.hide()
        }
    }
    openInteractiveTooltips.add(tooltipEl.value)
}

function handleAfterHide(event) {
    if (event.target !== tooltipEl.value) {
        return
    }
    openInteractiveTooltips.delete(tooltipEl.value)
}

let listeningEl = null

function stopListening() {
    if (!listeningEl) {
        return
    }
    listeningEl.removeEventListener('mouseover', cancelPendingHide)
    listeningEl.removeEventListener('wa-show', handleShow)
    listeningEl.removeEventListener('wa-after-hide', handleAfterHide)
    openInteractiveTooltips.delete(listeningEl)
    listeningEl = null
}

watch([tooltipEl, () => props.interactive], ([el, interactive]) => {
    stopListening()
    if (!el || !interactive) {
        return
    }
    el.addEventListener('mouseover', cancelPendingHide)
    el.addEventListener('wa-show', handleShow)
    el.addEventListener('wa-after-hide', handleAfterHide)
    listeningEl = el
})

onBeforeUnmount(stopListening)
</script>

<template>
    <wa-tooltip
        v-if="shouldShow"
        ref="tooltipEl"
        :hide-delay="interactive ? INTERACTIVE_HIDE_DELAY : undefined"
        v-bind="$attrs"
    >
        <slot />
    </wa-tooltip>
</template>
