<script setup>
// Small per-tab arrow opening a dropdown to place the tab into the center or one of the six
// docks. Menu only (no drag-and-drop). Lives inside a wa-tab / nav, so every WA event is
// stopped from bubbling to the surrounding tab group, and clicks never switch the tab.
import { PLACEMENT_OPTIONS, DOCK_LABELS, DOCK_ICONS } from './dockMeta'

defineProps({
    tabId: { type: String, required: true },
    current: { type: String, default: 'center' }, // dockId or 'center'
})
const emit = defineEmits(['place'])

function onSelect(event) {
    const dest = event.detail?.item?.value
    if (dest) emit('place', event.detail.item.value)
}
</script>

<template>
    <wa-dropdown
        class="placement-menu"
        placement="bottom-end"
        @click.stop
        @wa-select.stop="onSelect"
        @wa-show.stop
        @wa-hide.stop
        @wa-after-show.stop
        @wa-after-hide.stop
    >
        <wa-button
            slot="trigger"
            appearance="plain"
            size="small"
            class="placement-trigger"
            title="Place this tab"
            aria-label="Place this tab"
        >
            <wa-icon name="chevron-down"></wa-icon>
        </wa-button>

        <wa-dropdown-item disabled class="placement-header">Tab placement</wa-dropdown-item>
        <wa-divider></wa-divider>
        <wa-dropdown-item
            v-for="opt in PLACEMENT_OPTIONS"
            :key="opt"
            type="checkbox"
            :value="opt"
            :checked="opt === current"
        >
            <wa-icon slot="icon" :src="DOCK_ICONS[opt]"></wa-icon>
            {{ DOCK_LABELS[opt] }}
        </wa-dropdown-item>
    </wa-dropdown>
</template>

<style scoped>
.placement-menu {
    display: inline-flex;
}
.placement-trigger {
    --wa-form-control-padding-inline: 0.25em;
    font-size: 0.5em;
    opacity: 0.6;
}
.placement-trigger:hover {
    opacity: 1;
}
</style>
