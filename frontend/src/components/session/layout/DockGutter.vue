<script setup>
// One edge gutter (thin rail) holding the icons of collapsed / responsively-hidden docks.
// One icon PER TAB, anchored start/end mirroring the dock origin. A click dispatches the
// item's action (swap | restore | overlay) up to the layout.
import { computed, onUnmounted } from 'vue'

const props = defineProps({
    // resolver gutter: { edge, x, y, w, h, items: [{ dockId, tabs, action, anchor }] }
    gutter: { type: Object, required: true },
    openOverlayEdge: { type: String, default: null },
    // (item) -> the active tab id of a single rail dock — used by empty-area clicks so they act on the
    // dock's active (remembered) tab, exactly like clicking its active chip. See dockActiveTabId.
    resolveActiveTab: { type: Function, default: null },
})
const emit = defineEmits(['action'])

const style = computed(() => ({
    left: `${props.gutter.x}px`,
    top: `${props.gutter.y}px`,
    width: `${props.gutter.w}px`,
    height: `${props.gutter.h}px`,
}))

// Flatten items into per-tab icons, split into the start- and end-anchored groups.
function iconsFor(anchor) {
    const out = []
    for (const item of props.gutter.items) {
        if (item.anchor !== anchor) continue
        for (const tab of item.tabs) out.push({ item, tab })
    }
    return out
}
const startIcons = computed(() => iconsFor('start'))
const endIcons = computed(() => iconsFor('end'))

function isOpen(entry) {
    return entry.item.action === 'overlay' && props.openOverlayEdge === props.gutter.edge
}
function verb(entry) {
    if (entry.item.action === 'swap') return 'show this column'
    if (entry.item.action === 'restore') return 'restore'
    return isOpen(entry) ? 'close overlay' : 'peek overlay'
}
// A restore/swap chip's double-click maximizes its dock (see onClick); advertise it in the chip's
// title. Overlay chips don't maximize on double-click (their second click just forces the peek open),
// so they keep the plain single-action title.
function chipTitle(entry) {
    const base = `${entry.tab.label} — ${verb(entry)}`
    return entry.item.action === 'overlay' ? base : `${base} · double-click to maximize`
}
// Title for the rail's empty area (clicking anywhere acts on the pointed dock). The double-click hint
// only holds when a restore/swap dock is here — an overlay-only rail double-click just forces the peek.
const emptyAreaTitle = computed(() =>
    props.gutter.items.some((it) => it.action !== 'overlay')
        ? 'Click to open · double-click to maximize'
        : 'Click to open'
)
// Double-click on a rail chip = maximize that dock. A native dblclick is impossible on a restore/swap
// chip: its first click moves the dock out of the rail, so the chip is gone before a dblclick could
// fire. So we detect the double-click ourselves — hold the single action for DOUBLE_CLICK_DELAY and
// promote it to 'maximize' if a second click on the same chip lands first. Overlay chips stay instant
// (the peek isn't destructive); their second rapid click just forces the peek open so a double-click
// can't toggle it shut. Keep DOUBLE_CLICK_DELAY in sync with the rest of the layout's double-click feel.
const DOUBLE_CLICK_DELAY = 250 // ms

let pendingTimer = null
let pendingEntry = null
let lastOverlay = { key: null, at: 0 }

function chipKey(entry) { return entry.item.dockId + ':' + entry.tab.id }
function fire(entry, action) {
    emit('action', { edge: props.gutter.edge, dockId: entry.item.dockId, tabId: entry.tab.id, action })
}
function cancelPending() {
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null }
    pendingEntry = null
}
function flushPending() {
    if (!pendingTimer) return
    clearTimeout(pendingTimer); pendingTimer = null
    const e = pendingEntry; pendingEntry = null
    if (e) fire(e, e.item.action)
}

function onClick(entry) {
    if (entry.item.action === 'overlay') {
        // A pending restore/swap from another chip shouldn't be cancelled by an overlay click — let it run.
        flushPending()
        const key = chipKey(entry)
        const now = Date.now()
        if (lastOverlay.key === key && now - lastOverlay.at < DOUBLE_CLICK_DELAY) {
            lastOverlay = { key: null, at: 0 }
            fire(entry, 'overlay-open') // force open: a double-click leaves the peek open, never toggled shut
            return
        }
        lastOverlay = { key, at: now }
        fire(entry, entry.item.action) // normal peek toggle (instant)
        return
    }
    // restore / swap: defer the native action; a quick second click on the same chip becomes maximize.
    if (pendingEntry && chipKey(pendingEntry) === chipKey(entry)) {
        cancelPending()
        fire(entry, 'maximize')
        return
    }
    flushPending() // a different chip was pending — let its native action run, then defer this one
    pendingEntry = entry
    pendingTimer = setTimeout(() => {
        pendingTimer = null
        const e = pendingEntry; pendingEntry = null
        if (e) fire(e, e.item.action)
    }, DOUBLE_CLICK_DELAY)
}

// The empty area of the rail (anywhere that isn't a chip) acts on the dock it points at, on that
// dock's active tab — reusing the chip path so it gets the same deferred single/double handling. With
// at most two docks per edge, always on opposite anchors (start = top/left, end = bottom/right), the
// click position along the rail axis resolves the dock with no guessing: first half -> the start dock,
// second half -> the end dock.
function targetItemFor(event) {
    const items = props.gutter.items
    if (!items.length) return null
    if (items.length === 1) return items[0]
    const rect = event.currentTarget.getBoundingClientRect()
    const vertical = props.gutter.edge !== 'bottom'
    const rel = vertical
        ? (event.clientY - rect.top) / (rect.height || 1)
        : (event.clientX - rect.left) / (rect.width || 1)
    const wantAnchor = rel < 0.5 ? 'start' : 'end'
    return items.find((it) => it.anchor === wantAnchor) || items[0]
}
function onEmptyAreaClick(event) {
    if (event.target.closest('.g-chip')) return // a chip handles its own click
    const item = targetItemFor(event)
    if (!item) return
    const tabId = props.resolveActiveTab ? props.resolveActiveTab(item) : item.tabs[0]?.id
    if (!tabId) return
    onClick({ item, tab: { id: tabId } })
}

onUnmounted(cancelPending)
</script>

<template>
    <div class="dock-gutter" :class="gutter.edge" :style="style" :title="emptyAreaTitle" @click="onEmptyAreaClick">
        <div class="g-group start">
            <button
                v-for="entry in startIcons"
                :key="entry.item.dockId + ':' + entry.tab.id"
                type="button"
                class="g-chip"
                :class="{ open: isOpen(entry) }"
                :title="chipTitle(entry)"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span class="g-label">{{ entry.tab.label }}</span>
            </button>
        </div>
        <div class="g-group end">
            <button
                v-for="entry in endIcons"
                :key="entry.item.dockId + ':' + entry.tab.id"
                type="button"
                class="g-chip"
                :class="{ open: isOpen(entry) }"
                :title="chipTitle(entry)"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span class="g-label">{{ entry.tab.label }}</span>
            </button>
        </div>
    </div>
</template>

<style scoped>
.dock-gutter {
    --gutter-size: 30px; /* Keep updated with railW from layoutResolver.js */
    --gutter-padding: 2px;
    position: absolute;
    display: flex;
    justify-content: space-between;
    gap: var(--gutter-padding);
    padding: var(--gutter-padding);
    /* The whole rail is clickable (empty area opens the pointed dock; chips carry their own action). */
    cursor: pointer;
    background: var(--wa-color-surface-default, transparent); /* match .dock-region */
    z-index: 12; /* above an open overlay backdrop, so gutters stay clickable */
    --gutter-border: var(--divider-size) solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
}
/* Only the center-facing edge is bordered; the three edges on the layout boundary stay bare. */
.dock-gutter.left {
    flex-direction: column;
    align-items: center;
    border-right: var(--gutter-border);
}
.dock-gutter.right {
    flex-direction: column;
    align-items: center;
    border-left: var(--gutter-border);
}
.dock-gutter.bottom {
    flex-direction: row;
    align-items: center;
    border-top: var(--gutter-border);
}
.g-group {
    display: flex;
    gap: 2px;
}

.dock-gutter {
    .g-group {
        align-items: center;
        height: var(--gutter-size);
        position: absolute;
    }
    &.left, &.right {
        .g-group {
            transform: rotate(-90deg);
            &.start {
                top: calc(var(--gutter-padding) - var(--gutter-size));
                right: 0;
                transform-origin: bottom right;
            }
            &.end {
                bottom: calc(var(--gutter-padding) - var(--gutter-size));
                left: 0;
                transform-origin: top left;
            }
        }
    }
    &.bottom .g-group {
        top: var(--gutter-padding);
        &.start {
            left: var(--gutter-padding);
        }
        &.end {
            right: var(--gutter-padding);
        }
    }
}
.g-chip {
    display: inline-flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 5px;
    border-radius: var(--wa-border-radius-s, 4px);
    background: transparent;
    border: none;
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    padding: 3px 7px;
    font-size: 0.75rem;
    line-height: 1;
    white-space: nowrap;
    transition: background-color 0.15s, color 0.15s;
}
.g-chip wa-icon {
    flex: 0 0 auto;
    font-size: 1.1em;
    margin-inline-end: 0;
}
.g-chip:hover {
    color: inherit;
}
.g-chip.open {
    color: inherit;
}
</style>
