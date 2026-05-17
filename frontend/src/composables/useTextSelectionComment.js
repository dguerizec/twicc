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
 * @param {Ref<boolean>|ComputedRef<boolean>} options.enabled - When false, all listeners
 *   are detached and the widget is closed.
 */
export function useTextSelectionComment({ containerRef, isInScope = null, enabled }) {
    const textSelectionCommentRef = ref(null)
    const textSelectionText = ref('')
    const textSelectionPosition = ref(null)

    function resolveEl() {
        const c = containerRef.value
        if (!c) return null
        return c.$el ?? c
    }

    function close() {
        textSelectionPosition.value = null
        textSelectionText.value = ''
    }

    function selectionIsInScope(selection) {
        const anchor = selection?.anchorNode
        if (!anchor) return false
        const el = resolveEl()
        if (!el?.contains(anchor)) return false
        if (isInScope && !isInScope(selection)) return false
        return true
    }

    function handleMouseup() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const selection = window.getSelection()
        const text = selection?.toString()?.trim()
        if (!text) {
            textSelectionPosition.value = null
            return
        }
        if (!selectionIsInScope(selection)) {
            textSelectionPosition.value = null
            return
        }
        textSelectionText.value = text
        textSelectionPosition.value = getSelectionPosition()
    }

    function handleScroll() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const pos = getSelectionPosition()
        if (pos) {
            textSelectionPosition.value = pos
        } else {
            // Selection lost (nodes unmounted by virtual scroller, or cleared)
            close()
        }
    }

    function handleSelectionChange() {
        if (textSelectionCommentRef.value?.isExpanded) return
        const selection = window.getSelection()
        const text = selection?.toString()?.trim()
        if (!text) {
            if (textSelectionPosition.value) close()
            return
        }
        if (!selectionIsInScope(selection)) {
            if (textSelectionPosition.value) close()
            return
        }
        textSelectionText.value = text
        const pos = getSelectionPosition()
        if (pos) {
            textSelectionPosition.value = pos
        } else if (textSelectionPosition.value) {
            close()
        }
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
        closeTextSelectionComment: close,
    }
}
