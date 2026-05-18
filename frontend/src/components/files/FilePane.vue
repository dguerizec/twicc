<script setup>
import { ref, watch, computed, nextTick, useId, inject, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import { useSettingsStore } from '../../stores/settings'
import { usePanZoom } from '../../composables/usePanZoom'
import MarkdownContent from '../ui/MarkdownContent.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import CodeEditor from '../editor/CodeEditor.vue'
import DiffEditor from '../editor/DiffEditor.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'
import { useTextSelectionComment } from '../../composables/useTextSelectionComment'

const props = defineProps({
    projectId: String,
    sessionId: String,
    filePath: String,  // absolute path (also used in diff mode for language detection and save)
    isDraft: {
        type: Boolean,
        default: false,
    },
    /** Git commit SHA (null = index/uncommitted, undefined = not a git context) */
    commitSha: { type: String, default: undefined },
    // --- Diff mode props ---
    diffMode: {
        type: Boolean,
        default: false,
    },
    originalContent: {
        type: String,
        default: null,  // left side (null = new file, everything shows as added)
    },
    modifiedContent: {
        type: String,
        default: null,  // right side (null = deleted file, everything shows as removed)
    },
    diffReadOnly: {
        type: Boolean,
        default: true,  // true for commit diffs, false for index (editable)
    },
    apiPrefix: {
        type: String,
        default: null,
    },
    rootRestriction: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: true,
    },
    displayPath: {
        type: String,
        default: null,
    },
})

const emit = defineEmits(['revert'])

const filePathLabelId = useId()
const prevChangeButtonId = useId()
const nextChangeButtonId = useId()
const markdownPreviewButtonId = useId()
const svgPreviewButtonId = useId()
const viewInFilesButtonId = useId()
const searchButtonId = useId()

// Injected from SessionView: function to switch to Files tab and reveal a file.
// null when FilePane is not inside a SessionView (or no Files tab available).
const viewFileInFilesTab = inject('viewFileInFilesTab', null)

// Injected from SessionView: appends text to the session's message input.
// null when FilePane is not inside an active session — in that case the text
// selection comment widget stays disabled.
const insertTextAtCursor = inject('insertTextAtCursor', null)

// API prefix: use explicit prop when provided, otherwise project-level for drafts, session-level otherwise
const resolvedApiPrefix = computed(() => {
    if (props.apiPrefix) return props.apiPrefix
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

const commentContext = computed(() => {
    if (!props.filePath) return null
    if (props.commitSha !== undefined) {
        // Git context
        return {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentSessionId: '',
            source: 'git',
            sourceRef: props.commitSha ?? '',
            filePath: props.filePath,
            toolLineNum: null,
        }
    }
    // Files context
    return {
        projectId: props.projectId,
        sessionId: props.sessionId,
        subagentSessionId: '',
        source: 'files',
        sourceRef: '',
        filePath: props.filePath,
        toolLineNum: null,
    }
})

const settingsStore = useSettingsStore()

// --- CodeMirror editor instances ---
const codeEditorRef = ref(null)
const diffEditorRef = ref(null)

// --- Image pan/zoom ---
const imageRef = ref(null)
const { reset: resetZoom } = usePanZoom(imageRef)

// --- File content state ---
const currentContent = ref('')   // content currently in the editor
const loading = ref(false)       // true only for the very first load (no file displayed yet)
const switching = ref(false)     // true during file switch (editor stays visible)
const error = ref(null)
const isBinary = ref(false)
const fileSize = ref(0)
const imageSrc = ref(null)       // data URI for binary image files

// Whether the editor has ever successfully displayed a file.
// Used to distinguish "initial load" (show spinner, hide editor)
// from "file switch" (keep editor visible, show subtle indicator).
const hasLoadedOnce = ref(false)

// --- Markdown preview state ---
const isMarkdownFile = computed(() => {
    if (!props.filePath) return false
    return /\.(?:md|markdown|mdown|mkd|mkdn)$/i.test(props.filePath)
})
const showMarkdownPreview = ref(false)

// --- SVG preview state ---
const isSvgFile = computed(() => {
    if (!props.filePath) return false
    return /\.svg$/i.test(props.filePath)
})
const showSvgPreview = ref(false)
// Manually managed blob URL for SVG preview (revoked on change / unmount)
let _svgBlobUrl = null
const svgPreviewUrl = computed(() => {
    // Revoke previous blob URL if any
    if (_svgBlobUrl) {
        URL.revokeObjectURL(_svgBlobUrl)
        _svgBlobUrl = null
    }
    if (!showSvgPreview.value || !isSvgFile.value || !currentContent.value) return null
    const blob = new Blob([currentContent.value], { type: 'image/svg+xml' })
    _svgBlobUrl = URL.createObjectURL(blob)
    return _svgBlobUrl
})
onBeforeUnmount(() => {
    if (_svgBlobUrl) {
        URL.revokeObjectURL(_svgBlobUrl)
        _svgBlobUrl = null
    }
})

// --- Edit mode state ---
const isEditing = ref(false)
const isWritable = ref(false)
const saving = ref(false)
const saveError = ref(null)

const wordWrap = computed(() => settingsStore.isEditorWordWrap)
const sideBySide = computed(() => settingsStore.isDiffSideBySide)

// Auto-switch to unified when editor area is too narrow for side-by-side.
// Start at 0 so the default is unified (safe) until the first measurement arrives.
const SIDE_BY_SIDE_MIN_WIDTH = 900
const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
const widthMeasured = ref(false)
let resizeObserver = null

// Use a watcher on the template ref instead of onMounted so that the observer
// is connected as soon as the DOM element exists (handles conditional rendering).
watch(editorAreaRef, (el, _oldEl, onCleanup) => {
    resizeObserver?.disconnect()
    resizeObserver = null
    if (el) {
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                if (entry.contentRect.width > 0) {
                    editorAreaWidth.value = entry.contentRect.width
                    if (!widthMeasured.value) widthMeasured.value = true
                }
            }
        })
        resizeObserver.observe(el)
    }
    onCleanup(() => {
        resizeObserver?.disconnect()
        resizeObserver = null
    })
}, { flush: 'post' })

const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)
const effectiveSideBySide = computed(() => sideBySide.value && canSideBySide.value)

// Text selection comment widget: active whenever the pane is inside an active
// session — covers the markdown preview as well as the CodeMirror code/diff
// editors, all of which live under .editor-area.
//
// Firefox + CodeMirror have a known sync issue where the DOM native selection
// (`window.getSelection()`) doesn't reflect the CodeMirror internal selection
// after certain interactions (tab-switch sequences, gutter widget cycles).
// `getCmSelectionOverride()` reads the truthy selection directly from each
// active EditorView and is preferred by the composable when it returns data.
function forEachCmView(fn) {
    const ceView = codeEditorRef.value?.view
    if (ceView) fn(ceView)
    const de = diffEditorRef.value
    if (de) {
        const m = de.getModifiedView?.()
        const o = de.getOriginalView?.()
        if (m) fn(m)
        if (o) fn(o)
    }
}

function clearSourceSelection() {
    // Drop the DOM selection (covers markdown preview and the focused side effects).
    window.getSelection()?.removeAllRanges()
    // Also collapse each CodeMirror view's internal selection — focus shifts don't
    // clear it on their own, so without this the highlight would stay visible.
    forEachCmView((view) => {
        const head = view.state.selection.main.head
        view.dispatch({ selection: { anchor: head, head } })
    })
}

function getCmSelectionOverride() {
    const views = []
    const ceView = codeEditorRef.value?.view
    if (ceView) views.push(ceView)
    const de = diffEditorRef.value
    if (de) {
        const m = de.getModifiedView?.()
        const o = de.getOriginalView?.()
        if (m) views.push(m)
        if (o) views.push(o)
    }
    for (const view of views) {
        const sel = view.state.selection.main
        if (sel.empty) continue
        const text = view.state.doc.sliceString(sel.from, sel.to)
        if (!text.trim()) continue
        // Build a real DOM Range from CodeMirror's authoritative selection so we get
        // the exact bounding rect — same algorithm the native path uses, just bypassing
        // window.getSelection() (which Firefox doesn't keep in sync with CM here).
        try {
            const fromDom = view.domAtPos(sel.from)
            const toDom = view.domAtPos(sel.to)
            const range = document.createRange()
            range.setStart(fromDom.node, fromDom.offset)
            range.setEnd(toDom.node, toDom.offset)
            const rect = range.getBoundingClientRect()
            if (!rect.width && !rect.height) continue
            // Mirror the native logic: widget appears above only for backward
            // selections spanning multiple lines.
            const backward = sel.head < sel.anchor
            const above = backward && rect.height > 30
            const startLine = view.state.doc.lineAt(sel.from)
            const endLine = view.state.doc.lineAt(sel.to)
            // Trim the range at both edges so it matches what's visible in the
            // quoted excerpt:
            //  - end exactly at the start of a line → user took nothing from that line
            //  - start exactly at the end of a line → user took nothing from that line
            let lineFrom = startLine.number
            let lineTo = endLine.number
            if (lineTo > lineFrom && startLine.to === sel.from) lineFrom += 1
            if (lineTo > lineFrom && endLine.from === sel.to) lineTo -= 1
            return {
                text,
                anchor: view.dom,
                rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
                above,
                metadata: { filePath: props.filePath, lineFrom, lineTo },
            }
        } catch {
            continue
        }
    }
    return null
}

const {
    textSelectionCommentRef,
    textSelectionText,
    textSelectionPosition,
    textSelectionMetadata,
    closeTextSelectionComment,
    refreshSelection,
} = useTextSelectionComment({
    containerRef: editorAreaRef,
    getSelectionOverride: getCmSelectionOverride,
    enabled: computed(() => !!insertTextAtCursor),
})

// Whether the editor content differs from the last saved/fetched content.
// Delegates to the CodeMirror editor's own dirty tracking.
const isDirty = computed(() => {
    if (props.diffMode) return diffEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
})

// Extract filename from the absolute path for display in the header
const fileName = computed(() => {
    if (!props.filePath) return ''
    const parts = props.filePath.split('/')
    return parts[parts.length - 1]
})

// Whether the CodeMirror editor should be visible.
// Hidden when: no file selected, initial load (never displayed yet), binary, or error.
// In diff mode, content is passed via props so hasLoadedOnce is not relevant.
const showEditor = computed(() => {
    if (!props.filePath) return false
    if (props.diffMode) {
        // In diff mode, show if we have at least one side of the diff
        return props.originalContent !== null || props.modifiedContent !== null
    }
    if (!hasLoadedOnce.value) return false  // initial load — show spinner instead
    if (isBinary.value) return false
    if (error.value) return false
    return true
})

// Whether an image should be displayed (binary image or SVG preview)
const showImagePreview = computed(() => {
    if (imageSrc.value) return true  // binary image with data URI
    if (showSvgPreview.value && svgPreviewUrl.value) return true
    return false
})

// Whether the header toolbar should be visible.
// Hidden for binary images (no useful controls for them).
const showHeader = computed(() => {
    if (props.diffMode) return !!props.filePath
    if (isBinary.value) return false
    return !!props.filePath && hasLoadedOnce.value
})

// Whether a full-area placeholder should be shown (loading spinner, error, non-image binary).
// These overlay on top of the editor area.
const showOverlay = computed(() => {
    if (!props.filePath) return false
    if (loading.value) return true
    if (error.value) return true
    if (isBinary.value && !imageSrc.value) return true  // non-image binary
    return false
})

/**
 * Check if a file is writable without fetching its content.
 * Used in diff mode where content comes from props....
 */
async function checkWritable(filePath) {
    try {
        let url = `${resolvedApiPrefix.value}/file-content/?path=${encodeURIComponent(filePath)}`
        if (props.rootRestriction) url += `&root=${encodeURIComponent(props.rootRestriction)}`
        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            isWritable.value = !!data.writable
        }
    } catch {
        // Silently ignore — isWritable stays false
    }
}

/**
 * Fetch file content from the backend.
 *
 * @param {string} filePath - absolute path to fetch
 * @param {Object} [options]
 * @param {boolean} [options.isSwitch=false] - true when switching between files
 *   (editor stays visible). false for the very first load.
 */
async function fetchFileContent(filePath, { isSwitch = false } = {}) {
    if (isSwitch) {
        switching.value = true
    } else {
        loading.value = true
    }
    error.value = null
    saveError.value = null
    isWritable.value = false
    isBinary.value = false
    imageSrc.value = null

    try {
        let url = `${resolvedApiPrefix.value}/file-content/?path=${encodeURIComponent(filePath)}`
        if (props.rootRestriction) url += `&root=${encodeURIComponent(props.rootRestriction)}`
        const res = await apiFetch(url)
        const data = await res.json()

        if (!res.ok) {
            error.value = data.error || 'Failed to load file'
            currentContent.value = ''
            return
        }

        if (data.binary) {
            isBinary.value = true
            fileSize.value = data.size
            imageSrc.value = data.image_src || null
            currentContent.value = ''
            if (imageSrc.value) hasLoadedOnce.value = true
            return
        }

        if (data.error) {
            error.value = data.error
            currentContent.value = ''
            return
        }

        currentContent.value = data.content
        fileSize.value = data.size
        isWritable.value = !!data.writable
        hasLoadedOnce.value = true
        // Reset dirty state after a tick so CodeMirror has processed the new content
        nextTick(() => codeEditorRef.value?.resetDirty())
    } catch (err) {
        error.value = 'Network error: failed to load file'
        currentContent.value = ''
    } finally {
        loading.value = false
        switching.value = false
    }
}

watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        imageSrc.value = null
        // Reset edit mode when file is deselected
        isEditing.value = false
        return
    }

    resetZoom()

    // Reset edit mode and preview modes when switching files
    isEditing.value = false
    showMarkdownPreview.value = false
    showSvgPreview.value = false

    // In diff mode, content is passed via props — don't fetch.
    if (props.diffMode) {
        currentContent.value = props.modifiedContent ?? ''
        // For editable diffs (index view), check if the file is writable
        if (!props.diffReadOnly) {
            checkWritable(newPath)
        }
        return
    }

    // If we've already displayed a file before, this is a "switch" —
    // the editor stays mounted and visible while we fetch.
    await fetchFileContent(newPath, { isSwitch: hasLoadedOnce.value })
}, { immediate: true })

// In diff mode, when the parent re-fetches diff data (e.g. refresh),
// the modifiedContent prop changes. Reset edit mode and sync currentContent.
watch(() => props.modifiedContent, (newContent) => {
    if (!props.diffMode) return
    isEditing.value = false
    currentContent.value = newContent ?? ''
})

// In diff mode, when switching from read-only (commit) to editable (index)
// for the same file, filePath doesn't change so the main watch won't fire.
// Re-check writability when diffReadOnly transitions to false.
watch(() => props.diffReadOnly, (readOnly) => {
    if (!props.diffMode || readOnly || !props.filePath) return
    checkWritable(props.filePath)
})

// --- Edit mode handlers ---

function onEditToggle(event) {
    const checked = event.target.checked
    if (checked) {
        isEditing.value = true
        showMarkdownPreview.value = false  // exit preview when entering edit mode
        showSvgPreview.value = false       // exit SVG preview when entering edit mode
        saveError.value = null
    } else {
        // Revert silently when leaving edit mode
        revert()
        isEditing.value = false
    }
}

function toggleMarkdownPreview() {
    showMarkdownPreview.value = !showMarkdownPreview.value
}

function toggleSvgPreview() {
    showSvgPreview.value = !showSvgPreview.value
}

async function save() {
    if (saving.value || !props.filePath) return

    saving.value = true
    saveError.value = null

    // Use currentContent which is kept in sync via v-model (normal mode)
    // or @update:modified (diff mode).
    const content = currentContent.value

    try {
        const body = { path: props.filePath, content }
        if (props.rootRestriction) body.root = props.rootRestriction
        const res = await apiFetch(
            `${resolvedApiPrefix.value}/file-content/`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            }
        )
        const data = await res.json()

        if (!res.ok || data.error) {
            saveError.value = data.error || 'Failed to save file'
            return
        }

        // Success: reset dirty state as new baseline
        if (props.diffMode) {
            diffEditorRef.value?.resetDirty()
        } else {
            codeEditorRef.value?.resetDirty()
        }
    } catch (err) {
        saveError.value = 'Network error: failed to save file'
    } finally {
        saving.value = false
    }
}

/**
 * Revert editor content by re-fetching the file from the backend.
 * In diff mode, emits 'revert' so the parent can re-fetch the diff.
 * In normal mode, fetches fresh content directly.
 */
async function revert() {
    if (!props.filePath) return
    if (props.diffMode) {
        emit('revert')
        return
    }
    await fetchFileContent(props.filePath, { isSwitch: true })
}

function onWordWrapToggle(event) {
    settingsStore.setEditorWordWrap(event.target.checked)
}

function onSideBySideToggle(event) {
    settingsStore.setDiffSideBySide(event.target.checked)
}

/**
 * Reload the current file content from disk.
 * Safe to call at any time: skips reload in diff mode, when no file is
 * selected, or when the editor has unsaved changes (edit mode + dirty).
 */
async function reload() {
    if (props.diffMode || !props.filePath) return
    if (isEditing.value && isDirty.value) return
    await fetchFileContent(props.filePath, { isSwitch: true })
}

/**
 * Scroll the editor to a 1-based line number.
 * Delegates to the active editor (CodeEditor or DiffEditor).
 */
function scrollToLine(lineNum) {
    if (props.diffMode) {
        diffEditorRef.value?.scrollToLine(lineNum)
    } else {
        codeEditorRef.value?.scrollToLine(lineNum)
    }
}

// Whether the file content is currently being fetched (initial load or file switch)
const isLoading = computed(() => loading.value || switching.value)

// Expose dirty state, reload, scrollToLine, and loading state for parent components
defineExpose({ isDirty, isLoading, reload, scrollToLine })

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// --- CodeMirror editor event handlers ---

function onEditorReady({ view }) {
    // Post-mount setup if needed
}

function onDiffReady() {
    // Post-mount setup if needed
}

function onDiffModifiedChange(newContent) {
    currentContent.value = newContent
}

// --- Search (delegates to active editor) ---

function openSearch() {
    if (props.diffMode) {
        diffEditorRef.value?.openSearch()
    } else {
        codeEditorRef.value?.openSearch()
    }
}

// --- Diff navigation (delegates to DiffEditor) ---

function goToPreviousDiff() {
    diffEditorRef.value?.goToPreviousChunk()
}

function goToNextDiff() {
    diffEditorRef.value?.goToNextChunk()
}
</script>

<template>
    <div class="file-pane">
        <!-- File path bar (desktop only, when displayPath is provided) -->
        <template v-if="displayPath">
            <div class="file-path-header">
                <span class="file-path-label" :id="filePathLabelId">{{ displayPath }}</span>
                <AppTooltip :for="filePathLabelId">{{ displayPath }}</AppTooltip>
            </div>
            <wa-divider></wa-divider>
        </template>

        <!-- Header toolbar (visible once a file has been loaded) -->
        <div v-if="showHeader" class="header">
            <div class="header-left">
                <wa-button
                    v-if="!showMarkdownPreview && !showSvgPreview"
                    :id="searchButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="openSearch"
                >
                    <wa-icon name="magnifying-glass"></wa-icon>
                </wa-button>
                <AppTooltip :for="searchButtonId">Search in editor</AppTooltip>
                <!-- "View in Files tab" button: shown only in diff mode (Git tab context) -->
                <wa-button
                    v-if="viewFileInFilesTab && diffMode"
                    :id="viewInFilesButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="viewFileInFilesTab(filePath)"
                >
                    <wa-icon name="folder-open"></wa-icon>
                </wa-button>
                <AppTooltip :for="viewInFilesButtonId">View in Files tab</AppTooltip>
                <!-- Edit controls: hidden in read-only diff mode (commit diffs) -->
                <template v-if="(!diffMode || !diffReadOnly) && isWritable">
                    <wa-switch
                        :checked="isEditing"
                        size="small"
                        @change="onEditToggle"
                    >Edit</wa-switch>
                    <template v-if="isEditing">
                        <wa-button
                            size="small"
                            variant="brand"
                            :disabled="saving || !isDirty"
                            class="reduced-height"
                            @click="save"
                        >
                            <wa-spinner v-if="saving" slot="start"></wa-spinner>
                            Save
                        </wa-button>
                        <wa-button
                            size="small"
                            variant="neutral"
                            appearance="outlined"
                            :disabled="saving"
                            class="reduced-height"
                            @click="revert"
                        >Revert</wa-button>
                    </template>
                    <wa-button
                        v-else
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="header-spacer reduced-height"
                    >Spacer</wa-button>
                </template>
            </div>
            <div v-if="diffMode && !showMarkdownPreview" class="header-center">
                <div class="diff-nav-buttons">
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="prevChangeButtonId"
                        @click="goToPreviousDiff"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="prevChangeButtonId">Previous change</AppTooltip>
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="nextChangeButtonId"
                        @click="goToNextDiff"
                    >
                        <wa-icon name="arrow-down"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="nextChangeButtonId">Next change</AppTooltip>
                </div>
            </div>
            <div class="header-right">
                <wa-spinner v-if="switching" class="header-spinner"></wa-spinner>
                <wa-switch
                    v-if="!showMarkdownPreview && !showSvgPreview"
                    :checked="wordWrap"
                    size="small"
                    @change="onWordWrapToggle"
                >Wrap</wa-switch>
                <wa-switch
                    v-if="diffMode && !showMarkdownPreview && canSideBySide"
                    :checked="sideBySide"
                    size="small"
                    class="diff-layout-toggle"
                    @change="onSideBySideToggle"
                >Side by side</wa-switch>
                <!-- Markdown preview toggle: shown for .md files when not editing -->
                <wa-button
                    v-if="isMarkdownFile && !isEditing"
                    size="small"
                    variant="neutral"
                    :appearance="showMarkdownPreview ? 'filled' : 'outlined'"
                    :id="markdownPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMarkdownPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="markdownPreviewButtonId">Toggle markdown preview</AppTooltip>
                <!-- SVG preview toggle: shown for .svg files when not editing -->
                <wa-button
                    v-if="isSvgFile && !isEditing"
                    size="small"
                    variant="neutral"
                    :appearance="showSvgPreview ? 'filled' : 'outlined'"
                    :id="svgPreviewButtonId"
                    class="reduced-height"
                    @click="toggleSvgPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="svgPreviewButtonId">Toggle SVG preview</AppTooltip>
            </div>
        </div>

        <!-- Content area: editor is always mounted once, overlays sit on top -->
        <div ref="editorAreaRef" class="editor-area">
            <!-- Markdown preview (when toggled on for .md files) -->
            <div v-if="showMarkdownPreview && isMarkdownFile" class="markdown-preview-container">
                <MarkdownContent
                    :source="diffMode ? (modifiedContent ?? '') : currentContent"
                    :show-toolbar="false"
                />
            </div>

            <!-- Image preview (binary images or SVG preview) -->
            <div v-if="showImagePreview" class="image-preview-container">
                <img
                    ref="imageRef"
                    :src="imageSrc || svgPreviewUrl"
                    :alt="fileName"
                    class="image-preview"
                />
            </div>

            <!-- CodeMirror diff editor (diff mode) -->
            <DiffEditor
                v-if="diffMode && showEditor && !showMarkdownPreview && widthMeasured"
                ref="diffEditorRef"
                :original="originalContent ?? ''"
                :modified="currentContent"
                :file-path="filePath"
                :read-only="diffReadOnly || !isEditing"
                :word-wrap="wordWrap"
                :side-by-side="effectiveSideBySide"
                :collapse-unchanged="true"
                :comment-context="commentContext"
                @update:modified="onDiffModifiedChange"
                @save="save"
                @ready="onDiffReady"
                @cm-update="refreshSelection"
            />

            <!-- CodeMirror editor — mounted once, never destroyed on file switch -->
            <CodeEditor
                v-if="!diffMode"
                v-show="showEditor && !showMarkdownPreview && !showSvgPreview"
                ref="codeEditorRef"
                v-model="currentContent"
                :file-path="filePath"
                :read-only="!isEditing"
                :word-wrap="wordWrap"
                :line-numbers="true"
                :save-view-state="false"
                :comment-context="commentContext"
                @save="save"
                @ready="onEditorReady"
                @cm-update="refreshSelection"
            />

            <!-- Overlay: initial loading (before any file has been displayed) -->
            <div v-if="showOverlay" class="editor-overlay">
                <template v-if="loading">
                    <wa-spinner></wa-spinner>
                </template>
                <template v-else-if="error">
                    <wa-callout variant="danger" size="small">
                        {{ error }}
                    </wa-callout>
                </template>
                <template v-else-if="isBinary">
                    <wa-icon name="file-zipper" style="font-size: 1.5rem; opacity: 0.5;"></wa-icon>
                    <span>Binary file ({{ formatSize(fileSize) }}) cannot be displayed</span>
                </template>
            </div>
        </div>

        <!-- Save error message (overlays above the editor) -->
        <div v-if="saveError" class="editor-overlay">
            <wa-callout variant="danger" size="small">
                {{ saveError }}
            </wa-callout>
        </div>

        <!-- Ephemeral text selection comment widget (teleported to body to avoid overflow clipping) -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="textSelectionPosition"
                ref="textSelectionCommentRef"
                :selected-text="textSelectionText"
                :position="textSelectionPosition"
                :metadata="textSelectionMetadata"
                :clear-source-selection="clearSourceSelection"
                @close="closeTextSelectionComment"
            />
        </Teleport>
    </div>
</template>

<style scoped>
.file-pane {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.file-path-header {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    min-height: 1.5rem;
    flex-shrink: 0;
    background: var(--wa-color-surface-alt);

    & + wa-divider {
        flex-shrink: 0;
        --width: var(--divider-size);
        --spacing: 0;
    }
}

.file-path-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-bottom: var(--divider-size) solid var(--wa-color-surface-border);
    min-height: 2.25rem;
    flex-shrink: 0;
    flex-wrap: wrap;
}

.header-left {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.header-center {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.header-right {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.diff-nav-buttons {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.diff-nav-button::part(base) {
    padding: var(--wa-space-3xs) var(--wa-space-2xs);
}

.diff-layout-toggle {
    flex-shrink: 0;
}

.header-spinner {
    font-size: var(--wa-font-size-s);
    flex-shrink: 0;
}

.header-spacer {
    visibility: hidden;
    pointer-events: none;
}

.editor-area {
    flex: 1;
    position: relative;
    min-height: 0;
}

.markdown-preview-container {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    padding: var(--wa-space-m) var(--wa-space-l);
}

.image-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    display: flex;
    padding: var(--wa-space-m);
}

.image-preview {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    touch-action: none;
    margin: auto;
}

.editor-overlay {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 100%;
    color: var(--wa-color-neutral-500);
    font-size: var(--wa-font-size-s);
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
}
</style>
