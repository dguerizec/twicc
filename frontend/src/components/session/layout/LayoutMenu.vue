<script setup>
// Single chevron-down menu in the main-area tab nav, combining both layout actions:
//   • Save layout   (only when not single pane — there must be something to save)
//   • Select layout (a disabled header), then Single pane + the named catalog
// Lives in the tab nav, so every WA custom event is stopped from bubbling to the tab group.
import { computed } from 'vue'
import { useLayoutsStore, SINGLE_PANE_ID } from '../../../stores/layouts'

const props = defineProps({
    // Whether there is a layout to save (not single pane). Drives the "Save layout" entry.
    hasDocks: { type: Boolean, default: false },
})

const emit = defineEmits(['save', 'select', 'manage'])

// Uppercase sentinels for the action items — layout ids are always lowercased slugs, so they can
// never collide with a named layout's id.
const SAVE_VALUE = '__SAVE__'
const MANAGE_VALUE = '__MANAGE__'

const layoutsStore = useLayoutsStore()
const namedLayouts = computed(() => layoutsStore.getAllLayouts)

function onSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return
    if (value === SAVE_VALUE) emit('save')
    else if (value === MANAGE_VALUE) emit('manage')
    else emit('select', value)
}
</script>

<template>
    <wa-dropdown
        class="layout-menu"
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
            class="layout-menu-trigger reduced-height"
            appearance="plain"
            size="small"
            title="Layout"
            aria-label="Layout menu"
        >
            <wa-icon name="chevron-down"></wa-icon>
        </wa-button>

        <template v-if="props.hasDocks">
            <wa-dropdown-item :value="SAVE_VALUE">Save layout</wa-dropdown-item>
            <wa-divider></wa-divider>
        </template>

        <wa-dropdown-item disabled class="layout-menu-header">Select layout</wa-dropdown-item>
        <wa-dropdown-item :value="SINGLE_PANE_ID">Single pane</wa-dropdown-item>
        <wa-dropdown-item v-for="l in namedLayouts" :key="l.id" :value="l.id">{{ l.name }}</wa-dropdown-item>

        <template v-if="namedLayouts.length">
            <wa-divider></wa-divider>
            <wa-dropdown-item :value="MANAGE_VALUE">
                <wa-icon slot="icon" name="sliders"></wa-icon>
                Manage layouts…
            </wa-dropdown-item>
        </template>
    </wa-dropdown>
</template>

<style scoped>
.layout-menu-trigger {
    --wa-form-control-padding-inline: 0.3em;
}
</style>
