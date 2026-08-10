// useTextSelectionComment — shared logic for the floating text-selection
// comment widget. Detects text selection inside a container, computes a
// viewport position, follows scroll, and respects an optional scope filter.
// The consumer renders <TextSelectionComment> via <Teleport to="body">
// and binds the refs returned here.

import { ref, watch, onBeforeUnmount, unref } from 'vue'

function isSelectionBackward(selection) {
    if (!selection.anchorNode || !selection.focusNode) return false
    if (selection.anchorNode === selection.focusNode) {
        return selection.focusOffset < selection.anchorOffset
    }
    const position = selection.anchorNode.compareDocumentPosition(selection.focusNode)
    return !!(position & Node.DOCUMENT_POSITION_PRECEDING)
}

function getSelectionPosition() {
    try {
        const selection = window.getSelection()
        if (!selection?.rangeCount) return null
        const range = selection.getRangeAt(0)
        const rect = range.getBoundingClientRect()
        // Rect can be zero-size if the selected nodes were unmounted (virtual scroller recycling)
        if (!rect.width && !rect.height) return null
        // Show above only for backward selections spanning multiple lines
        const above = isSelectionBackward(selection) && rect.height > 30
        return { top: above ? rect.top : rect.bottom, left: rect.left + rect.width / 2, above }
    } catch {
        return null
    }
}

/**
 * @param {object} options
 * @param {Ref} options.containerRef - Ref to the scrollable container element (or Vue
 *   component exposing $el). The mouseup listener is attached to this element, the
 *   scroll listener follows it, and selections outside it are ignored.
 * @param {(selection: Selection) => boolean} [options.isInScope] - Extra metier filter
 *   applied after the container containment check (e.g. exclude CodeMirror zones).
 * @param {() => ({ text: string, anchor: Node, rect: DOMRectInit } | null)} [options.getSelectionOverride] -
 *   Optional source of truth that takes priority over `window.getSelection()`. Used to
 *   work around Firefox + CodeMirror cases where the DOM native selection isn't kept in
 *   sync with CodeMirror's internal selection. Should return null when no relevant
 *   selection is active.
 * @param {(anchor: Node, selection: Selection) => (object | null)} [options.enrichNativeMetadata] -
 *   Optional hook called when the native (window.getSelection) path produces a result.
 *   Receives the anchor node and the live selection, and returns metadata to attach
 *   (e.g. file path for a selection inside a rendered markdown preview, or the quoting
 *   mode when the container mixes prose and code views). The selection is needed to
 *   check that a candidate element holds the whole range, not just its start.
 * @param {Ref<boolean>|ComputedRef<boolean>} options.enabled - When false, all listeners
 *   are detached and the widget is closed.
 */
export function useTextSelectionComment({ containerRef, isInScope = null, getSelectionOverride = null, enrichNativeMetadata = null, enabled }) {
    const textSelectionCommentRef = ref(null)
    const textSelectionText = ref('')
    const textSelectionPosition = ref(null)
    // Optional consumer-provided metadata about the selection (e.g. file path +
    // line range for CodeMirror selections). Surfaced to the widget so it can
    // enrich the formatted comment.
    const textSelectionMetadata = ref(null)

    function resolveEl() {
        const c = containerRef.value
        if (!c) return null
        return c.$el ?? c
    }

    function close() {
        textSelectionPosition.value = null
        textSelectionText.value = ''
        textSelectionMetadata.value = null
    }

    function anchorIsInScope(anchor, selection = null) {
        if (!anchor) return false
        const el = resolveEl()
        if (!el?.contains(anchor)) return false
        if (isInScope && selection && !isInScope(selection)) return false
        return true
    }

    /**
     * Build a selection info object — { text, anchor, position } — from either the
     * override source (CodeMirror-driven) or the native DOM selection. Returns null
     * when there is nothing actionable.
     */
    function readSelectionInfo() {
        // Override takes priority. Used to bypass window.getSelection() when it lies
        // (Firefox + CodeMirror sync gap).
        if (typeof getSelectionOverride === 'function') {
            const ov = getSelectionOverride()
            if (ov && ov.text && ov.rect) {
                const text = ov.text.trim()
                if (text) {
                    const rect = ov.rect
                    const above = !!ov.above
                    return {
                        text,
                        anchor: ov.anchor,
                        metadata: ov.metadata ?? null,
                        position: {
                            top: above ? rect.top : rect.bottom,
                            left: (rect.left + rect.right) / 2,
                            above,
                        },
                    }
                }
            }
        }
        // Native fallback
        const sel = window.getSelection()
        if (!sel?.rangeCount) return null
        const text = sel.toString()?.trim()
        if (!text) return null
        const range = sel.getRangeAt(0)
        const rect = range.getBoundingClientRect()
        if (!rect.width && !rect.height) return null
        const above = isSelectionBackward(sel) && rect.height > 30
        const metadata = typeof enrichNativeMetadata === 'function'
            ? (enrichNativeMetadata(sel.anchorNode, sel) ?? null)
            : null
        return {
            text,
            anchor: sel.anchorNode,
            nativeSelection: sel,
            metadata,
            position: {
                top: above ? rect.top : rect.bottom,
                left: rect.left + rect.width / 2,
                above,
            },
        }
    }

    function handleMouseup() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const info = readSelectionInfo()
        if (!info || !anchorIsInScope(info.anchor, info.nativeSelection)) {
            textSelectionPosition.value = null
            textSelectionMetadata.value = null
            return
        }
        textSelectionText.value = info.text
        textSelectionPosition.value = info.position
        textSelectionMetadata.value = info.metadata ?? null
    }

    function handleScroll() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const info = readSelectionInfo()
        if (info && anchorIsInScope(info.anchor, info.nativeSelection)) {
            textSelectionPosition.value = info.position
        } else {
            // Selection lost (nodes unmounted by virtual scroller, or cleared)
            close()
        }
    }

    function handleSelectionChange() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const info = readSelectionInfo()
        if (!info || !anchorIsInScope(info.anchor, info.nativeSelection)) {
            if (textSelectionPosition.value) close()
            return
        }
        textSelectionText.value = info.text
        textSelectionPosition.value = info.position
        textSelectionMetadata.value = info.metadata ?? null
    }

    // Track current attachment so we can move listeners when the container element
    // changes (e.g. a v-if'd container being remounted) or when `enabled` flips.
    let attachedEl = null
    let docAttached = false
    let scrollEl = null

    function attachMouseupTo(el) {
        if (attachedEl === el) return
        if (attachedEl) attachedEl.removeEventListener('mouseup', handleMouseup)
        if (el) el.addEventListener('mouseup', handleMouseup)
        attachedEl = el
    }

    function attachDocListener() {
        if (docAttached) return
        document.addEventListener('selectionchange', handleSelectionChange)
        docAttached = true
    }

    function detachDocListener() {
        if (!docAttached) return
        document.removeEventListener('selectionchange', handleSelectionChange)
        docAttached = false
    }

    watch(
        [() => unref(enabled), () => resolveEl()],
        ([isEnabled, el]) => {
            if (!isEnabled) {
                attachMouseupTo(null)
                detachDocListener()
                close()
                return
            }
            attachDocListener()
            attachMouseupTo(el)
        },
        { immediate: true },
    )

    // Scroll listener follows widget visibility (only attach while the floating button is shown).
    watch(textSelectionPosition, (pos, oldPos) => {
        if (pos && !oldPos) {
            const el = resolveEl()
            if (el) {
                el.addEventListener('scroll', handleScroll, { passive: true })
                scrollEl = el
            }
        } else if (!pos && oldPos && scrollEl) {
            scrollEl.removeEventListener('scroll', handleScroll)
            scrollEl = null
        }
    })

    onBeforeUnmount(() => {
        attachMouseupTo(null)
        detachDocListener()
        if (scrollEl) {
            scrollEl.removeEventListener('scroll', handleScroll)
            scrollEl = null
        }
    })

    return {
        textSelectionCommentRef,
        textSelectionText,
        textSelectionPosition,
        textSelectionMetadata,
        closeTextSelectionComment: close,
        // Re-evaluate the current selection on demand. Works around Firefox +
        // CodeMirror cases where the native `selectionchange` event isn't
        // dispatched when CodeMirror finalizes its selection; consumers can
        // call this after a CM update to force the widget to refresh.
        refreshSelection: handleSelectionChange,
    }
}
