import { onBeforeUnmount, onMounted } from 'vue'
import { useFramePoolStore } from '../stores/framePool'

/**
 * Flag divider drags of a <wa-split-panel> into the frame pool so FrameHost
 * can neutralize iframe pointer-events for the duration (an iframe would
 * otherwise capture pointermove and freeze the drag). The docking gutters
 * have their own wiring in SessionLayout.vue; this covers the three plain
 * wa-split-panels (project sidebar, FilesPanel tree/content, GitPanel
 * tree/content) whose drags over an iframe are broken today already.
 */
export function useSplitDividerDragFlag(splitPanelRef) {
    const pool = useFramePoolStore()
    let dragging = false

    function onPointerDown(event) {
        const onDivider = event
            .composedPath()
            .some((node) => node?.getAttribute?.('part')?.split(' ').includes('divider'))
        if (!onDivider) return
        dragging = true
        pool.beginDividerDrag()
    }

    function onPointerEnd() {
        if (!dragging) return
        dragging = false
        pool.endDividerDrag()
    }

    onMounted(() => {
        splitPanelRef.value?.addEventListener('pointerdown', onPointerDown)
        window.addEventListener('pointerup', onPointerEnd, true)
        window.addEventListener('pointercancel', onPointerEnd, true)
    })
    onBeforeUnmount(() => {
        splitPanelRef.value?.removeEventListener('pointerdown', onPointerDown)
        window.removeEventListener('pointerup', onPointerEnd, true)
        window.removeEventListener('pointercancel', onPointerEnd, true)
        onPointerEnd() // never leave the depth stuck if unmounted mid-drag
    })
}
