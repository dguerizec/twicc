<script setup>
// GlobalMediaPreview.vue - Singleton wrapper around MediaPreviewDialog.
//
// One instance is mounted in App.vue and driven by the useMediaPreview
// composable's module-scoped state. Any code can call `openMediaPreview(items)`
// from anywhere to open the dialog without having to instantiate it locally.
//
// This is the dedicated entry point for markdown-embedded images and SVGs
// (Mermaid diagrams), where a per-component dialog would mean potentially
// hundreds of dialog instances in the DOM at once.
import { ref, watch, nextTick } from 'vue'
import MediaPreviewDialog from './MediaPreviewDialog.vue'
import { items, currentIndex, isOpen, closeMediaPreview } from '../../composables/useMediaPreview'

const dialogRef = ref(null)

// Drive the dialog from the singleton state. The dialog itself manages its
// own internal currentIndex once opened (for prev/next navigation); we only
// seed the initial position via `open(index)`.
//
// The `nextTick` matters: `openMediaPreview()` flips `items`, `currentIndex`
// AND `isOpen` in the same tick, but the wa-dialog measures its `fit-content`
// width at the moment we call `open()`. Without waiting for Vue to flush the
// items update into the dialog's <img>, wa-dialog measures an empty body and
// snapshots a near-zero width — opening a thumbnail-sized panel.
watch(isOpen, async (open) => {
    if (!dialogRef.value) return
    if (open) {
        await nextTick()
        // The watcher may race with a fast close-then-open sequence; bail
        // out if the user has already toggled isOpen back to false.
        if (!isOpen.value || !dialogRef.value) return
        dialogRef.value.open(currentIndex.value)
    } else {
        dialogRef.value.close()
    }
})

// Sync state back when the dialog hides for any reason (Escape, light-dismiss,
// programmatic close). Without this, isOpen would stay true after a manual
// dismissal and the next openMediaPreview() call wouldn't retrigger the watcher.
function onDialogClose() {
    if (isOpen.value) closeMediaPreview()
}
</script>

<template>
    <MediaPreviewDialog
        ref="dialogRef"
        :items="items"
        @close="onDialogClose"
    />
</template>
