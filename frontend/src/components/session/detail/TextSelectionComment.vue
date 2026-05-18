<script setup>
// TextSelectionComment.vue — Ephemeral floating widget for commenting on selected
// text in the session view. Shows a small button on text selection; clicking it
// expands a panel with a textarea + "Add to message" action. Nothing is persisted
// to IndexedDB — the user must send the message right away.

import { ref, inject, nextTick, computed, onMounted, onBeforeUnmount } from 'vue'
import { formatComment } from '../../../stores/codeComments'
import { useSettingsStore } from '../../../stores/settings'
import { toast } from '../../../composables/useToast'

const props = defineProps({
    /** The text the user selected in the session view. */
    selectedText: { type: String, required: true },
    /** Viewport-relative position: { top, left, above } — anchored at selection edge. */
    position: { type: Object, required: true },
    /** When true, skip the collapsed button and open the panel immediately on mount. */
    autoExpand: { type: Boolean, default: false },
    /** Optional source suffix for the formatted output (e.g. "from terminal"). */
    sourceLabel: { type: String, default: '' },
    /** Optional source metadata used to enrich the formatted comment.
     *  Shape: { filePath: string, lineFrom: number, lineTo: number }. */
    metadata: { type: Object, default: null },
    /** Function called when the widget needs to dismiss the source selection
     *  (on Comment/Copy clicks). Defaults to clearing the DOM selection, which is
     *  enough for normal HTML; consumers (e.g. FilePane) override it to also clear
     *  the internal CodeMirror selection so the behavior is consistent there. */
    clearSourceSelection: { type: Function, default: () => window.getSelection()?.removeAllRanges() },
})

const emit = defineEmits(['close'])

const insertTextAtCursor = inject('insertTextAtCursor', null)
const settingsStore = useSettingsStore()

const placeholderText = computed(() => {
    if (settingsStore.isTouchDevice) return 'Optional comment...'
    const keys = settingsStore.isMac ? '⌘↵ or Ctrl↵' : 'Ctrl↵ or Meta↵'
    return `Optional comment... (${keys} to add to message)`
})

const expanded = ref(false)
const commentText = ref('')
const textareaRef = ref(null)
const rootRef = ref(null)
const isDragging = ref(false)

// Combined pixel offset (drag + clamp corrections) from the base position.
const panelOffset = ref({ dx: 0, dy: 0 })

const rootStyle = computed(() => {
    const base = { left: props.position.left + 'px' }
    const above = props.position.above
    if (above) {
        // Anchor from bottom: place widget's bottom edge 4px above position.top
        const vh = window.visualViewport?.height ?? window.innerHeight
        base.bottom = (vh - props.position.top + 4) + 'px'
    } else {
        base.top = (props.position.top + 4) + 'px'
    }
    if (expanded.value) {
        const { dx, dy } = panelOffset.value
        base.transform = `translate(calc(-50% + ${dx}px), ${dy}px)`
    } else {
        base.transform = 'translateX(-50%)'
    }
    return base
})

const canAdd = computed(() => !!insertTextAtCursor)

// ─── Viewport clamping ─────────────────────────────────────────────

/**
 * Measure the panel's rendered rect and nudge it back into the visible viewport.
 * Adds a correction delta to the current panelOffset (preserves user drag).
 * Uses visualViewport when available so it accounts for the mobile keyboard.
 */
function clampToViewport() {
    const el = rootRef.value
    if (!el) return

    const rect = el.getBoundingClientRect()
    const margin = 8
    const vv = window.visualViewport
    const vw = vv?.width ?? window.innerWidth
    const vh = vv ? (vv.offsetTop + vv.height) : window.innerHeight

    let dx = 0
    let dy = 0

    if (rect.left < margin) dx = margin - rect.left
    else if (rect.right > vw - margin) dx = (vw - margin) - rect.right

    if (rect.bottom > vh - margin) dy = (vh - margin) - rect.bottom
    if (rect.top + dy < margin) dy = margin - rect.top

    if (dx || dy) {
        panelOffset.value = {
            dx: panelOffset.value.dx + dx,
            dy: panelOffset.value.dy + dy,
        }
    }
}

/** Re-clamp when the mobile keyboard opens/closes. */
function onVisualViewportResize() {
    if (expanded.value) clampToViewport()
}

// ─── Drag (panel background as handle) ────────────────────────────

let dragStart = null

function onDragPointerDown(e) {
    // Only primary button / single touch
    if (e.button !== 0) return
    // Don't drag when interacting with quote (scrollable), textareas, or buttons
    const target = e.target
    if (target.closest('.tsc-quote, wa-textarea, wa-button, textarea, button')) return
    e.preventDefault()
    isDragging.value = true
    dragStart = { x: e.clientX, y: e.clientY, ...panelOffset.value }
    document.addEventListener('pointermove', onDragPointerMove)
    document.addEventListener('pointerup', onDragPointerUp)
}

function onDragPointerMove(e) {
    if (!dragStart) return
    panelOffset.value = {
        dx: dragStart.dx + (e.clientX - dragStart.x),
        dy: dragStart.dy + (e.clientY - dragStart.y),
    }
}

function onDragPointerUp() {
    dragStart = null
    isDragging.value = false
    document.removeEventListener('pointermove', onDragPointerMove)
    document.removeEventListener('pointerup', onDragPointerUp)
    clampToViewport()
}

// ─── Expand / close ────────────────────────────────────────────────

function expand() {
    panelOffset.value = { dx: 0, dy: 0 }
    expanded.value = true
    // Dismiss the source selection so the highlight goes away. In plain HTML the
    // browser already drops the page selection when the textarea below gains focus,
    // but for CodeMirror sources the internal selection persists — the consumer's
    // override clears it for consistency.
    props.clearSourceSelection?.()
    window.visualViewport?.addEventListener('resize', onVisualViewportResize)
    nextTick(() => {
        clampToViewport()
        textareaRef.value?.focus()
    })
}

function close() {
    window.visualViewport?.removeEventListener('resize', onVisualViewportResize)
    emit('close')
}

function addToMessage() {
    if (!canAdd.value) return

    const formatted = formatComment(
        {
            lineText: props.selectedText,
            content: commentText.value,
            filePath: props.metadata?.filePath,
            lineFrom: props.metadata?.lineFrom,
            lineTo: props.metadata?.lineTo,
        },
        { isSelectedText: true, sourceLabel: props.sourceLabel },
    )
    insertTextAtCursor(formatted + '\n')
    close()
}

function copy() {
    navigator.clipboard.writeText(props.selectedText)
    toast.success('Copied to clipboard', { duration: 2000 })
    props.clearSourceSelection?.()
    close()
}

function handleKeydown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        addToMessage()
        return
    }
    if (e.key === 'Escape') {
        e.stopPropagation()
        close()
    }
}

// Close on any mousedown outside the widget
function handleDocumentMousedown(e) {
    if (rootRef.value?.contains(e.target)) return
    close()
}

onMounted(() => {
    document.addEventListener('mousedown', handleDocumentMousedown, true)
    if (props.autoExpand) expand()
})

onBeforeUnmount(() => {
    document.removeEventListener('mousedown', handleDocumentMousedown, true)
    document.removeEventListener('pointermove', onDragPointerMove)
    document.removeEventListener('pointerup', onDragPointerUp)
    window.visualViewport?.removeEventListener('resize', onVisualViewportResize)
})

defineExpose({ isExpanded: expanded })
</script>

<template>
    <div
        ref="rootRef"
        class="text-selection-comment"
        :class="{ expanded }"
        :style="rootStyle"
    >
        <!-- Collapsed: comment + copy buttons -->
        <div v-if="!expanded" class="tsc-collapsed">
            <wa-button
                class="tsc-trigger"
                variant="brand"
                appearance="filled-outlined"
                size="small"
                @mousedown.prevent
                @click.stop="expand"
            >
                <wa-icon name="comment" variant="regular"></wa-icon>
            </wa-button>
            <wa-button
                class="tsc-trigger"
                variant="neutral"
                appearance="filled"
                size="small"
                @mousedown.prevent
                @click.stop="copy"
            >
                <wa-icon name="copy" variant="regular"></wa-icon>
            </wa-button>
        </div>

        <!-- Expanded: comment panel (background is the drag handle) -->
        <div
            v-else
            class="tsc-panel"
            :class="{ dragging: isDragging }"
            @keydown="handleKeydown"
            @pointerdown="onDragPointerDown"
        >
            <!-- Selected text preview (scrollable) -->
            <div class="tsc-quote">{{ selectedText }}</div>

            <wa-textarea
                ref="textareaRef"
                :value="commentText"
                @input="commentText = $event.target.value"
                :placeholder="placeholderText"
                size="small"
                rows="3"
            ></wa-textarea>

            <div class="tsc-help">
                <strong>Note:</strong> Clicking outside this dialog will discard the selection and any comment entered. Drag here to move the dialog.
            </div>

            <div class="tsc-actions">
                <wa-button size="small" variant="neutral" appearance="outlined" @click="close">
                    Cancel
                </wa-button>
                <wa-button
                    size="small"
                    variant="brand"
                    appearance="outlined"
                    :disabled="!canAdd"
                    @click="addToMessage"
                >
                    Add to message
                </wa-button>
            </div>
        </div>
    </div>
</template>

<style scoped>
.text-selection-comment {
    position: fixed;
    z-index: 10000;
    /* transform is set dynamically in rootStyle based on selection direction */
}

/* ── Collapsed: side-by-side comment + copy buttons ──────────────── */

.tsc-collapsed {
    display: flex;
    gap: var(--wa-space-xs);
}

/* ── Panel ───────────────────────────────────────────────────────── */

.tsc-panel {
    width: 20rem;
    max-width: calc(100vw - 2rem);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-default);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    box-shadow: var(--wa-shadow-l);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    cursor: grab;
    touch-action: none; /* prevent scroll while dragging on mobile */
}

.tsc-panel.dragging {
    cursor: grabbing;
}

/* ── Selected text quote ─────────────────────────────────────────── */

.tsc-quote {
    max-height: 6.4em; /* ~4 lines */
    padding: var(--wa-space-xs) var(--wa-space-xs);
    border-left: 3px solid var(--wa-color-brand);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-lowered);
    font-size: var(--wa-font-size-s);
    line-height: 1.4;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    cursor: auto;
    touch-action: auto; /* allow native scroll within quote */
}

/* ── Help text ───────────────────────────────────────────────────── */

.tsc-help {
    font-size: var(--wa-font-size-s);
    line-height: 1.3;
    color: var(--wa-color-text-quiet);
}

/* ── Actions ─────────────────────────────────────────────────────── */

.tsc-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-xs);
}
</style>
