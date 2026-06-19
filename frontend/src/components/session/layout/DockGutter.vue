<script setup>
// One edge gutter (thin rail) holding the icons of collapsed / responsively-hidden docks.
// One icon PER TAB, anchored start/end mirroring the dock origin. A click dispatches the
// item's action (swap | restore | overlay) up to the layout.
import { computed } from 'vue'

const props = defineProps({
    // resolver gutter: { edge, x, y, w, h, items: [{ dockId, tabs, action, anchor }] }
    gutter: { type: Object, required: true },
    openOverlayEdge: { type: String, default: null },
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
function onClick(entry) {
    emit('action', {
        edge: props.gutter.edge,
        dockId: entry.item.dockId,
        tabId: entry.tab.id,
        action: entry.item.action,
    })
}
</script>

<template>
    <div class="dock-gutter" :class="gutter.edge" :style="style">
        <div class="g-group start">
            <button
                v-for="entry in startIcons"
                :key="entry.item.dockId + ':' + entry.tab.id"
                type="button"
                class="g-icon"
                :class="{ open: isOpen(entry) }"
                :title="`${entry.tab.label} — ${verb(entry)}`"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
            </button>
        </div>
        <div class="g-group end">
            <button
                v-for="entry in endIcons"
                :key="entry.item.dockId + ':' + entry.tab.id"
                type="button"
                class="g-icon"
                :class="{ open: isOpen(entry) }"
                :title="`${entry.tab.label} — ${verb(entry)}`"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
            </button>
        </div>
    </div>
</template>

<style scoped>
.dock-gutter {
    position: absolute;
    display: flex;
    justify-content: space-between;
    gap: 0.25rem;
    padding: 0.25rem;
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
    gap: 0.25rem;
}
.dock-gutter.left .g-group,
.dock-gutter.right .g-group {
    flex-direction: column;
    align-items: center;
}
.dock-gutter.bottom .g-group {
    flex-direction: row;
    align-items: center;
}
.g-icon {
    width: 1.5rem;
    height: 1.5rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--wa-border-radius-s, 4px);
    background: var(--wa-color-surface-default, rgba(0, 0, 0, 0.06));
    border: 1px solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
    color: inherit;
    cursor: pointer;
    padding: 0;
}
.g-icon:hover {
    border-color: var(--wa-color-brand-border-loud, var(--wa-color-primary-500));
}
.g-icon.open {
    background: var(--wa-color-brand-fill-loud, var(--wa-color-primary-500));
    color: var(--wa-color-brand-on-loud, #fff);
}
</style>
