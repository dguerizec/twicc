<!-- frontend/src/components/CodeEditor.vue -->
<!-- Wraps CodeMirror 6's EditorView as a Vue 3 component. -->
<template>
    <div ref="editorEl" class="code-editor"></div>
</template>

<script setup>
import { ref, shallowRef, toRefs, nextTick, watch, inject, onMounted, onBeforeUnmount } from 'vue'
import { EditorView, keymap } from '@codemirror/view'
import { EditorSelection, Transaction, Compartment } from '@codemirror/state'
import { undoDepth } from '@codemirror/commands'
import { resolveLanguage, detectIndent, useCodeMirrorExtensions, useSettingsWatcher, toggleSearchPanel } from '../../composables/useCodeMirror'
import { createCodeCommentsExtension, syncCommentsEffect } from '../../extensions/codeComments'
import { useSettingsStore } from '../../stores/settings'
import { useCodeCommentsStore, formatComment, formatAllComments } from '../../stores/codeComments'

// ─── View state cache (module-level, shared across instances) ────────────────

/** @type {Map<string, { scrollTop: number, selection: import('@codemirror/state').EditorSelection }>} */
const viewStateCache = new Map()

// ─── Props ───────────────────────────────────────────────────────────────────

const props = defineProps({
    modelValue: { type: String, default: '' },
    filePath: { type: String, default: null },
    language: { type: String, default: null },
    readOnly: { type: Boolean, default: false },
    wordWrap: { type: Boolean, default: false },
    lineNumbers: { type: Boolean, default: true },
    saveViewState: { type: Boolean, default: false },
    extensions: { type: Array, default: () => [] },
    /** Comment context for inline annotations. Null = comments disabled.
     *  Shape: { source: 'files'|'git'|'tool', filePath: string, commitSha: string|null, toolUseId: string|null } */
    commentContext: { type: Object, default: null },
})

// ─── Emits ───────────────────────────────────────────────────────────────────

const emit = defineEmits(['update:modelValue', 'save', 'ready', 'cm-update'])

// ─── Template ref & state ────────────────────────────────────────────────────

const editorEl = ref(null)

/** Use shallowRef so Vue doesn't deeply proxy the EditorView object. */
const view = shallowRef(null)

/** The undoDepth at the last save/load point. Set to -1 when unreachable (branch diverged). */
let _savedUndoDepth = 0

/** True when the current undo history position differs from the saved point. */
const isDirty = ref(false)

/** Flag to break the echo loop: set true when we emit an update, cleared next tick. */
let _internalUpdate = false

/** Cleanup function returned by useSettingsWatcher. */
let _stopSettingsWatcher = null

// ─── Helpers ─────────────────────────────────────────────────────────────────

function saveCurrentViewState(filePath) {
    if (!filePath || !view.value) return
    const state = view.value.state
    viewStateCache.set(filePath, {
        scrollTop: view.value.scrollDOM.scrollTop,
        selection: state.selection,
    })
}

function restoreViewState(filePath) {
    if (!filePath || !view.value) return
    const saved = viewStateCache.get(filePath)
    if (!saved) return

    view.value.scrollDOM.scrollTop = saved.scrollTop
    // Clamp selection to current doc length to avoid out-of-range errors
    const docLen = view.value.state.doc.length
    const ranges = saved.selection.ranges.map(r => {
        const anchor = Math.min(r.anchor, docLen)
        const head = Math.min(r.head, docLen)
        return EditorSelection.range(anchor, head)
    })
    view.value.dispatch({
        selection: EditorSelection.create(ranges, saved.selection.mainIndex),
    })
}

// ─── Language resolution ─────────────────────────────────────────────────────

async function applyLanguage(filePath, language) {
    const langExtension = await resolveLanguage(filePath, language)
    if (view.value) {
        cmExtensions.reconfigure(view.value, 'language', langExtension)
    }
}

// ─── Extension setup (called once in onMounted) ───────────────────────────────

const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)
const { readOnly, wordWrap, lineNumbers } = toRefs(props)
const cmExtensions = useCodeMirrorExtensions(
    { readOnly, wordWrap, lineNumbers },
    { initialTheme: settingsStore.getEffectiveColorScheme, initialFontSize: settingsStore.getFontSize },
)

const commentCompartment = new Compartment()

function buildCommentExt(context) {
    if (!context) return []
    const existingComments = codeCommentsStore.getCommentsForContext(context)
    return createCodeCommentsExtension({
        initialComments: existingComments.map(c => ({ lineNumber: c.lineNumber, content: c.content, lineText: c.lineText || '' })),
        getContent: (lineNumber) => {
            const comments = codeCommentsStore.getCommentsForContext(context)
            const comment = comments.find(c => c.lineNumber === lineNumber)
            return comment?.content ?? ''
        },
        onAdd: (lineNumber, lineText) => codeCommentsStore.addComment(context, lineNumber, lineText),
        onUpdate: (lineNumber, content) => codeCommentsStore.updateComment(context, lineNumber, content),
        onRemove: (lineNumber) => codeCommentsStore.removeComment(context, lineNumber),
        onAddToMessage: insertTextAtCursor ? (lineNumber) => {
            const comments = codeCommentsStore.getCommentsForContext(context)
            const comment = comments.find(c => c.lineNumber === lineNumber)
            if (comment) {
                insertTextAtCursor(formatComment(comment) + '\n')
                codeCommentsStore.removeComment(context, lineNumber)
            }
        } : null,
        onAddAllToMessage: insertTextAtCursor ? () => {
            const allComments = codeCommentsStore.getCommentsBySession(context.projectId, context.sessionId)
            if (allComments.length > 0) {
                insertTextAtCursor(formatAllComments(allComments) + '\n')
                codeCommentsStore.removeAllSessionComments(context.projectId, context.sessionId)
            }
        } : null,
        getSessionCommentCount: () => codeCommentsStore.getCommentsBySession(context.projectId, context.sessionId)
                .filter(c => c.content.trim()).length,
    })
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

onMounted(async () => {
    // 1. Resolve language
    const langExtension = await resolveLanguage(props.filePath, props.language)

    // 2. Build update listener
    const updateListener = EditorView.updateListener.of((update) => {
        if (update.docChanged) {
            const depth = undoDepth(update.state)
            // If undoDepth drops below saved point, we've undone past it
            // and any new edit will branch — mark as unreachable
            if (depth < _savedUndoDepth) {
                _savedUndoDepth = -1
            }
            isDirty.value = depth !== _savedUndoDepth
            _internalUpdate = true
            emit('update:modelValue', update.state.doc.toString())
            nextTick(() => { _internalUpdate = false })
        }
        // Surface selection/focus changes so consumers (e.g. FilePane's text
        // selection widget) can re-read the DOM selection on demand. Works
        // around Firefox cases where `selectionchange` isn't fired when CM
        // finalizes the DOM selection.
        if (update.selectionSet || update.focusChanged) {
            emit('cm-update')
        }
    })

    // 3. Build Ctrl+S keymap
    const saveKeymap = keymap.of([{
        key: 'Mod-s',
        run: () => {
            if (!props.readOnly && isDirty.value) emit('save')
            // Always return true to prevent browser's native Save dialog
            return true
        },
    }])

    // 4. Assemble full extension array
    const allExtensions = [
        ...cmExtensions.extensions,
        saveKeymap,
        updateListener,
        commentCompartment.of(buildCommentExt(props.commentContext)),
        ...props.extensions,
    ]

    // 6. Create EditorView
    // Force root: document so that style-mod injects CSS into the document head,
    // not into a Web Awesome shadow root (CM6's getRoot() follows assignedSlot
    // and lands in wa-split-panel's shadow DOM, where styles don't apply to
    // our light DOM editor). See https://github.com/codemirror/dev/issues/178
    view.value = new EditorView({
        doc: props.modelValue,
        extensions: allExtensions,
        parent: editorEl.value,
        root: document,
    })

    // 7. Apply resolved language into the compartment
    if (langExtension) {
        cmExtensions.reconfigure(view.value, 'language', langExtension)
    }

    // 7b. Detect and apply indent unit from file content
    cmExtensions.reconfigure(view.value, 'indentUnit', detectIndent(props.modelValue))

    // 8. Set up reactive theme/fontSize watchers
    _stopSettingsWatcher = useSettingsWatcher(() => view.value, cmExtensions)

    // 9. Emit ready
    emit('ready', { view: view.value })
})

onBeforeUnmount(() => {
    if (props.saveViewState && props.filePath) {
        saveCurrentViewState(props.filePath)
    }
    if (_stopSettingsWatcher) {
        _stopSettingsWatcher()
    }
    view.value?.destroy()
})

// ─── Watchers ────────────────────────────────────────────────────────────────

// External content update (v-model)
watch(() => props.modelValue, (newValue) => {
    if (_internalUpdate || !view.value) return
    const currentContent = view.value.state.doc.toString()
    if (newValue === currentContent) return

    // Replace entire document content (not undoable — this is an external load, not a user edit)
    view.value.dispatch({
        changes: {
            from: 0,
            to: currentContent.length,
            insert: newValue ?? '',
        },
        annotations: Transaction.addToHistory.of(false),
    })
    // External content change = new clean state (undoDepth is unchanged since addToHistory: false)
    _savedUndoDepth = undoDepth(view.value.state)
    isDirty.value = false
    // Re-detect indent style for the new content
    cmExtensions.reconfigure(view.value, 'indentUnit', detectIndent(newValue))
})

// File path change: re-resolve language, optionally save/restore view state
watch(() => props.filePath, async (newPath, oldPath) => {
    if (props.saveViewState && oldPath) {
        saveCurrentViewState(oldPath)
    }

    await applyLanguage(newPath, props.language)

    if (props.saveViewState && newPath) {
        await nextTick()
        restoreViewState(newPath)
    } else if (view.value) {
        // No view state to restore — scroll to top
        await nextTick()
        view.value.scrollDOM.scrollTop = 0
    }
})

// Explicit language override change
watch(() => props.language, async (newLang) => {
    await applyLanguage(props.filePath, newLang)
})

// readOnly toggle
watch(() => props.readOnly, (newReadOnly) => {
    if (!view.value) return
    cmExtensions.reconfigure(view.value, 'readOnly', newReadOnly)
})

// wordWrap toggle
watch(() => props.wordWrap, (newWordWrap) => {
    if (!view.value) return
    cmExtensions.reconfigure(view.value, 'wordWrap', newWordWrap)
})

// lineNumbers toggle
watch(() => props.lineNumbers, (newLineNumbers) => {
    if (!view.value) return
    cmExtensions.reconfigure(view.value, 'lineNumbers', newLineNumbers)
})

// Code comments: reconfigure extension when context changes
watch(() => props.commentContext, (newContext) => {
    if (!view.value) return
    view.value.dispatch({
        effects: commentCompartment.reconfigure(buildCommentExt(newContext)),
    })
})

// Code comments: sync decorations when store changes (handles late hydration)
watch(
    () => props.commentContext ? codeCommentsStore.getCommentsForContext(props.commentContext) : null,
    (comments) => {
        if (!view.value || !comments) return
        view.value.dispatch({
            effects: syncCommentsEffect.of(
                comments.map(c => ({ lineNumber: c.lineNumber, content: c.content, lineText: c.lineText || '' }))
            ),
        })
    },
)

// Code comments: broadcast session-wide "with content" count changes to CM6 widgets.
// This watcher reacts to ALL comment content changes across the session (any source,
// any file) and bridges Pinia reactivity → CM6 widget DOM via a custom event.
// It only fires on threshold transitions (empty↔non-empty), not on every keystroke.
watch(
    () => {
        if (!props.commentContext) return 0
        return codeCommentsStore.getCommentsBySession(
            props.commentContext.projectId, props.commentContext.sessionId
        ).filter(c => c.content.trim()).length
    },
    (newCount) => {
        if (!view.value) return
        view.value.dom.dispatchEvent(new CustomEvent(
            'code-comment-count-changed', { detail: { count: newCount } }
        ))
    },
)

// ─── Exposed API ─────────────────────────────────────────────────────────────

/**
 * Scroll the editor so that the given 1-based line number is visible,
 * placing it near the center of the viewport and making it the active line.
 */
function scrollToLine(lineNum) {
    const v = view.value
    if (!v) return
    const lineCount = v.state.doc.lines
    const clampedLine = Math.max(1, Math.min(lineNum, lineCount))
    const line = v.state.doc.line(clampedLine)
    v.dispatch({
        selection: EditorSelection.cursor(line.from),
        effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    })
}

defineExpose({
    view,
    isDirty,
    scrollToLine,
    resetDirty() {
        _savedUndoDepth = view.value ? undoDepth(view.value.state) : 0
        isDirty.value = false
    },
    focus() { view.value?.focus() },
    openSearch() { toggleSearchPanel(view.value) },
})
</script>

<style scoped>
.code-editor {
    width: 100%;
    height: 100%;
}

.code-editor :deep(.cm-editor) {
    height: 100%;
}
</style>
