/**
 * useMediaPreview - Centralized "open a media item in a preview dialog" flow.
 *
 * Single entry point for any UI that needs to show one or more media items
 * (images, SVGs) fullscreen with prev/next navigation. A single
 * <GlobalMediaPreview> instance is mounted in App.vue and reads this state.
 *
 * Item shape: { type: 'image', src: <url|data url>, name?: string, link?: string }
 * - `link` is optional. When set, the dialog shows an "Open link" button.
 */
import { ref } from 'vue'

// Module-scoped: shared across every consumer of the composable.
export const items = ref([])
export const currentIndex = ref(0)
export const isOpen = ref(false)

/**
 * Open the preview dialog with a list of items, starting at the given index.
 */
export function openMediaPreview(newItems, index = 0) {
    if (!Array.isArray(newItems) || newItems.length === 0) return
    const clamped = Math.max(0, Math.min(index, newItems.length - 1))
    items.value = newItems
    currentIndex.value = clamped
    isOpen.value = true
}

/**
 * Close the preview dialog.
 */
export function closeMediaPreview() {
    isOpen.value = false
}
