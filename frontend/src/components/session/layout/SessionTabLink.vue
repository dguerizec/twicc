<script setup>
// A real link inside a session's wa-tab (or a gutter chip). The browser owns
// modified clicks, middle-clicks, context menus and native link dragging. Only
// a plain primary click is cancelled so the surrounding tab/layout machinery
// can keep using Vue Router without a document reload.
const props = defineProps({
    href: { type: String, required: true },
    // Links nested in wa-tab stay out of the tab order because wa-tab owns the
    // keyboard interaction. Standalone gutter links opt in to normal link focus.
    tabbable: { type: Boolean, default: false },
})

const emit = defineEmits(['plain-click'])

function isPlainPrimaryClick(event) {
    return event.button === 0
        && !event.ctrlKey
        && !event.metaKey
        && !event.shiftKey
        && !event.altKey
}

function onClick(event) {
    if (isPlainPrimaryClick(event)) {
        event.preventDefault()
        // Clicking the nested anchor would otherwise leave focus on it and
        // break wa-tab-group's arrow-key navigation, which expects wa-tab to
        // own focus. Standalone gutter links have no surrounding wa-tab.
        event.currentTarget.closest('wa-tab')?.focus({ preventScroll: true })
        emit('plain-click', event)
        return
    }

    // A modified click still performs the anchor's native action, but must not
    // bubble into wa-tab-group and also select the tab in this browser window.
    event.stopPropagation()
}

function onDoubleClick(event) {
    // Preserve the layout's plain double-click-to-maximize gesture. A modified
    // double click belongs to the native link interaction and must not mutate
    // the layout in the current window.
    if (!isPlainPrimaryClick(event)) event.stopPropagation()
}
</script>

<template>
    <a
        class="session-tab-link"
        :href="href"
        :tabindex="props.tabbable ? 0 : -1"
        @click="onClick"
        @dblclick="onDoubleClick"
    ><slot /></a>
</template>

<style scoped>
.session-tab-link {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
    color: inherit;
    text-decoration: none;
}
</style>
