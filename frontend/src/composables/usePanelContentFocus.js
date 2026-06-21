import { watch } from 'vue'

// A tool panel (Files / Git) focuses its primary content on a :focus-request bump from the layout: the
// open file's viewer (FilePane) when one is shown, otherwise the tree's search filter (FileTreePanel).
//
// The catch: the host sub-component can mount LATE. On the FIRST activation the panel fetches its data
// before rendering — Git's FileTreePanel sits behind v-else-if="entries.length > 0", which only becomes
// true once the git log has loaded (~2s). So when the focus-request watcher fires, BOTH refs are still
// null; a plain `ref.value?.focus…()` silently drops the request and the child's own useFocusRetry pump
// never even starts. By the time the sub-panel mounts, nothing is waiting — hence "focus works on every
// activation except the first". (Files has the same code; it just happens to mount its tree fast enough.)
//
// Fix: hold the request as pending and (re)apply it whenever a target ref appears — until it lands. The
// chosen child's pump then handles the rest (waiting for its own field to render, fighting reveal steals).
// Branch choice mirrors the original watcher: a file open at request time → its viewer, else the filter;
// once we've handed off to a child we stop (we don't re-steal focus if a file opens later).
export function usePanelContentFocus({ focusRequest, selectedFile, filePaneRef, fileTreePanelRef }) {
    let pending = false

    function apply() {
        if (!pending) return
        if (selectedFile.value && filePaneRef.value) {
            filePaneRef.value.focusContent()
            pending = false
        } else if (fileTreePanelRef.value) {
            fileTreePanelRef.value.focusSearchInput()
            pending = false
        }
        // else: neither host mounted yet — stay pending; the ref watcher re-applies when one appears.
    }

    watch(focusRequest, () => {
        pending = true
        apply()
    })
    // Re-apply when a host sub-panel mounts (ref null → instance) or the file-vs-tree branch flips.
    watch([filePaneRef, fileTreePanelRef, selectedFile], apply)
}
