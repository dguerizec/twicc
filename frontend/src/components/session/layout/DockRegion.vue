<script setup>
// One shown dock region (a side column slice or the bottom). Renders a native WA tab bar
// (nav-only, like TerminalPanel) with a per-tab placement arrow and a far-right minimize
// button; the body is a Teleport target the parent fills with the real panel(s). The region
// holds one dockId (split) or two (merged) — its body is registered under each.
import { computed, ref, watchEffect } from 'vue'
import TabPlacementMenu from './TabPlacementMenu.vue'

const props = defineProps({
    region: { type: Object, required: true },
    activeTabId: { type: String, default: null },
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select', 'minimize', 'place', 'pane-focus'])

const bodyRef = ref(null)

const style = computed(() => ({
    left: `${props.region.x}px`,
    top: `${props.region.y}px`,
    width: `${props.region.w}px`,
    height: `${props.region.h}px`,
}))
const tabs = computed(() => props.region.slots.flatMap((s) => s.tabs))
const dockIds = computed(() => props.region.slots.map((s) => s.dockId))

function dockOfTab(tabId) {
    const slot = props.region.slots.find((s) => s.tabs.some((t) => t.id === tabId))
    return slot ? slot.dockId : 'center'
}

// Register the body element as the teleport target for every dock this region hosts.
watchEffect((onCleanup) => {
    const el = bodyRef.value
    if (!el) return
    const ids = dockIds.value
    for (const d of ids) props.registerTarget(`region:${d}`, el)
    onCleanup(() => { for (const d of ids) props.unregisterTarget(`region:${d}`) })
})

function onShow(event) { emit('select', event.detail.name) }

// Click-to-focus: interacting anywhere in a pane should make its tab the route owner. We listen
// on the CLICK (capture phase, so it fires even if the panel stops propagation — e.g. xterm),
// NOT pointerdown: a click is the END of the gesture, after any file-select / terminal-tab change
// it produced. This is a *deferred* focus request (see SessionView.requestPaneFocus), superseded
// by any real navigation from the same gesture — so a navigating click wins and a plain click
// falls through to focusing the pane's current state, with no competing navigation.
function onBodyClick() {
    if (props.activeTabId) emit('pane-focus', props.activeTabId)
}
</script>

<template>
    <div class="dock-region" :class="region.kind" :style="style">
        <wa-tab-group class="dock-tabnav" :active="activeTabId" @wa-tab-show.stop="onShow">
            <wa-tab v-for="t in tabs" :key="t.id" slot="nav" :panel="t.id" class="dock-tab">
                <wa-icon v-if="t.icon" :name="t.icon" class="dock-tab-icon"></wa-icon>
                <span class="dock-tab-label">{{ t.label }}</span>
                <TabPlacementMenu
                    :tab-id="t.id"
                    :current="dockOfTab(t.id)"
                    @place="(dest) => emit('place', t.id, dest)"
                />
            </wa-tab>
            <wa-button
                slot="nav"
                class="dock-minimize"
                appearance="plain"
                size="small"
                title="Minimize to gutter"
                aria-label="Minimize to gutter"
                @click.stop="emit('minimize', dockIds)"
            >
                <wa-icon name="window-minimize"></wa-icon>
            </wa-button>
        </wa-tab-group>
        <div ref="bodyRef" class="dock-body" @click.capture="onBodyClick"></div>
    </div>
</template>

<style scoped>
.dock-region {
    position: absolute;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
    background: var(--wa-color-surface-default, transparent);
    min-width: 0;
    min-height: 0;
}

/* wa-tab-group used only for its nav — hide its (empty) body, like TerminalPanel */
.dock-tabnav {
    flex: 0 0 auto;
    min-width: 0;
    overflow: hidden;
    --indicator-color: var(--wa-color-primary-500);
    --track-color: transparent;
    --track-width: 2px;
}
.dock-tabnav::part(base) {
    overflow: hidden;
}
.dock-tabnav::part(body) {
    display: none;
}
.dock-tabnav::part(nav) {
    border-bottom: 1px solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
    padding-bottom: 0;
}
.dock-tabnav::part(tabs) {
    align-items: center;
}
.dock-tab {
    display: inline-flex;
}
.dock-tab::part(base) {
    display: inline-flex;
    align-items: center;
    gap: 0.35em;
    padding: 0.25em 0.6em;
}
.dock-tab-icon {
    font-size: 0.85em;
}
.dock-minimize {
    margin-inline-start: auto;
    --wa-form-control-padding-inline: 0.3em;
}

.dock-body {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
</style>
