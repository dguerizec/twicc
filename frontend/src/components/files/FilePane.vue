<script setup>
import { ref, watch, computed, nextTick, useId, inject, onMounted, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import { useSettingsStore } from '../../stores/settings'
import { useCommandRegistry } from '../../composables/useCommandRegistry'
import { usePanZoom } from '../../composables/usePanZoom'
import MarkdownContent from '../ui/MarkdownContent.vue'
import MermaidDiagram from '../ui/MermaidDiagram.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import CodeEditor from '../editor/CodeEditor.vue'
import DiffEditor from '../editor/DiffEditor.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'
import ArtifactBookmarkButton from '../artifacts/ArtifactBookmarkButton.vue'
import { useDataStore } from '../../stores/data'
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
    // Auto-enable the markdown / SVG preview when opening such a file (the eye
    // toggle stays, just defaulted on). Used by the Artifacts tab.
    previewByDefault: {
        type: Boolean,
        default: false,
    },
    displayPath: {
        type: String,
        default: null,
    },
    // Render-only artifact mode: locks the preview on and hides the source/eye
    // toggles + the Edit switch (used by the Artifacts browser view).
    renderOnly: {
        type: Boolean,
        default: false,
    },
    // When set (the session that owns this artifact), FilePane shows the artifact
    // bookmark toggle for renderable artifacts. Null outside the Artifacts context.
    artifactBookmarkSessionId: {
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
const htmlPreviewButtonId = useId()
const htmlPreviewReloadButtonId = useId()
const mermaidPreviewButtonId = useId()
const viewInFilesButtonId = useId()
const searchButtonId = useId()
const editSwitchId = useId()

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

// --- HTML preview state ---
const isHtmlFile = computed(() => {
    if (!props.filePath) return false
    return /\.html?$/i.test(props.filePath)
})
const showHtmlPreview = ref(false)
// Bumped to force the preview <iframe> to reload. The iframe renders the file
// as served from disk (the raw endpoint), not the editor buffer, so unsaved
// edits are reflected only after saving and reloading.
const htmlPreviewReloadKey = ref(0)

// base64url-encode a string (unicode-safe) for the standalone raw URL's
// confinement-root path segment.
function base64UrlEncode(str) {
    const bytes = new TextEncoder().encode(str)
    let binary = ''
    for (const byte of bytes) binary += String.fromCharCode(byte)
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

// URL of the raw-serving endpoint for the current file. The file path lives in
// the URL *path* (not a query param) so an <iframe> resolves a page's relative
// CSS/JS/asset references to sibling raw URLs. Project scope uses the
// project/session prefix; standalone scope (Artifacts tab) carries the
// confinement root as a base64url path segment. Used by the HTML, PDF, audio
// and video previews.
const rawFileUrl = computed(() => {
    if (!props.filePath) return null
    const trailing = props.filePath
        .replace(/^\/+/, '')
        .split('/')
        .map(encodeURIComponent)
        .join('/')
    if (props.projectId) {
        return `${resolvedApiPrefix.value}/file-raw/${trailing}`
    }
    const rootB64 = base64UrlEncode(props.rootRestriction || '')
    return `/api/file-raw/${rootB64}/${trailing}`
})

// The HTML preview adds a cache-bust token so the reload button forces a fresh
// load. Relative-asset resolution drops the query, so siblings are unaffected.
const htmlPreviewSrc = computed(() => {
    if (!isHtmlFile.value || !rawFileUrl.value) return null
    return `${rawFileUrl.value}?_=${htmlPreviewReloadKey.value}`
})

// Whether an HTML page is currently shown in the preview iframe (vs the source
// editor). Exposed so the Artifacts tab can decide to live-reload it.
const isHtmlPreviewActive = computed(() => isHtmlFile.value && showHtmlPreview.value)

// --- Mermaid preview state (.mmd / .mermaid — rendered to a pan/zoomable SVG) ---
const isMermaidFile = computed(() => /\.(?:mmd|mermaid)$/i.test(props.filePath || ''))
const showMermaidPreview = ref(false)

// Whether any preview overlay (Markdown / SVG / HTML / Mermaid) is active.
// A preview is a read-only rendering, so the editing affordances (Search,
// Edit toggle, Save/Revert) are irrelevant and hidden while it's on; only the
// preview "eye" stays reachable to toggle back to the source.
const isPreviewing = computed(() =>
    showMarkdownPreview.value || showSvgPreview.value || showHtmlPreview.value || showMermaidPreview.value
)

// --- Binary media (PDF / audio / video) — rendered straight from the raw
// endpoint, which streams with no size cap. They are binary, need no
// file-content fetch and have no source view. ---
const isPdfFile = computed(() => /\.pdf$/i.test(props.filePath || ''))
const isAudioFile = computed(() => /\.(?:mp3|wav|ogg|oga|opus|m4a|aac|flac|weba)$/i.test(props.filePath || ''))
const isVideoFile = computed(() => /\.(?:mp4|m4v|webm|ogv|mov)$/i.test(props.filePath || ''))
const isBinaryMediaFile = computed(() => isPdfFile.value || isAudioFile.value || isVideoFile.value)

// Renderable artifact = any type FilePane shows rendered (no source view).
// Images are detected after load via imageSrc (the binary-image data URI).
const isRenderableArtifact = computed(() =>
    isMarkdownFile.value || isSvgFile.value || isHtmlFile.value || isMermaidFile.value
    || isBinaryMediaFile.value || !!imageSrc.value)

// Path of the current file relative to the artifacts root (rootRestriction),
// used as the bookmark key. Null when the file is not under the root.
const relativeArtifactPath = computed(() => {
    const root = props.rootRestriction
    const fp = props.filePath
    if (!root || !fp) return null
    const prefix = root.endsWith('/') ? root : root + '/'
    return fp.startsWith(prefix) ? fp.slice(prefix.length) : null
})

const dataStore = useDataStore()
// The bookmark for the artifact currently shown (when in artifact context),
// used to surface its name next to the path and reflect the toggle state.
const artifactBookmark = computed(() =>
    props.artifactBookmarkSessionId && relativeArtifactPath.value
        ? dataStore.artifactBookmarkFor(props.artifactBookmarkSessionId, relativeArtifactPath.value)
        : null,
)

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

function getMarkdownPreviewMetadata(anchor) {
    // Selections inside the rendered markdown preview don't carry line numbers
    // (the source line range isn't recoverable from rendered HTML), but we can
    // still surface the file path to disambiguate the message.
    if (!anchor || !props.filePath) return null
    const el = anchor.nodeType === Node.ELEMENT_NODE ? anchor : anchor.parentElement
    if (el?.closest?.('.markdown-preview-container')) {
        return { filePath: props.filePath }
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
    enrichNativeMetadata: getMarkdownPreviewMetadata,
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
    // Binary media (PDF, audio, video) render as a player with no source view —
    // no toolbar, like a rendered image.
    if (isBinaryMediaFile.value) return false
    // HTML previews render in an <iframe> independent of the source fetch, so
    // keep the toolbar (preview/source toggle, reload) reachable right away —
    // even if the source content hasn't loaded (or failed to load).
    if (isHtmlFile.value) return !!props.filePath
    return !!props.filePath && hasLoadedOnce.value
})

// Whether the Edit switch is currently available — mirrors the switch's own
// v-if exactly: the header is visible, the file is writable, and we're not in
// a read-only diff (commit diffs). Gates both the Alt+E shortcut and the
// command-palette entry.
const canEdit = computed(() =>
    showHeader.value && (!props.diffMode || !props.diffReadOnly) && isWritable.value
)

// Whether a full-area placeholder should be shown (loading spinner, error, non-image binary).
// These overlay on top of the editor area.
const showOverlay = computed(() => {
    if (!props.filePath) return false
    // The HTML preview <iframe> loads independently of the source fetch; never
    // cover it with the source loading spinner / fetch error placeholder.
    if (showHtmlPreview.value && isHtmlFile.value) return false
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

    // Reset edit mode and preview modes when switching files. previewByDefault
    // (Artifacts tab) opens md/svg files directly in preview; the eye toggle
    // still lets the user switch to raw.
    isEditing.value = false
    const previewOn = props.previewByDefault || props.renderOnly
    showMarkdownPreview.value = previewOn && isMarkdownFile.value
    showSvgPreview.value = previewOn && isSvgFile.value
    showHtmlPreview.value = previewOn && isHtmlFile.value
    showMermaidPreview.value = previewOn && isMermaidFile.value

    // In diff mode, content is passed via props — don't fetch.
    if (props.diffMode) {
        currentContent.value = props.modifiedContent ?? ''
        // For editable diffs (index view), check if the file is writable
        if (!props.diffReadOnly) {
            checkWritable(newPath)
        }
        return
    }

    // Binary media (PDF, audio, video) render straight from the raw endpoint;
    // skip the file-content fetch — it would read up to its 5 MB cap or error
    // on larger media, while the player streams from file-raw with no cap.
    if (isBinaryMediaFile.value) {
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        loading.value = false
        switching.value = false
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

function setEditing(enabled) {
    if (enabled) {
        isEditing.value = true
        showMarkdownPreview.value = false  // exit preview when entering edit mode
        showSvgPreview.value = false       // exit SVG preview when entering edit mode
        showHtmlPreview.value = false      // exit HTML preview when entering edit mode
        showMermaidPreview.value = false   // exit Mermaid preview when entering edit mode
        saveError.value = null
    } else {
        // Revert silently when leaving edit mode
        revert()
        isEditing.value = false
    }
}

function onEditToggle(event) {
    setEditing(event.target.checked)
}

// Toggle edit mode programmatically (Alt+E shortcut and command palette).
// No-op when editing isn't available for the current file/pane.
function toggleEdit() {
    if (!canEdit.value) return
    setEditing(!isEditing.value)
}

// --- Alt+E shortcut + command palette entry ---
//
// Both the global Alt+E handler (App.vue) and the palette command target the
// *active* editable pane. Only one FilePane is ever `active` at a time (the
// visible tab of the active session/project view), so guarding on
// `props.active && canEdit` selects exactly the editor the user is looking at.

const { registerCommand, unregisterCommand } = useCommandRegistry()

// Unique per instance: every FilePane would otherwise share one id and an
// unmounting/deactivating pane could unregister the active pane's command.
const toggleEditCommandId = `display.toggle-file-edit.${useId()}`

// Alt+E is dispatched as a window event by App.vue's global key handler. It
// carries `detail.handled`, which we flip so App.vue knows to swallow the key
// (and the browser's native Edit menu / macOS dead-key accent) only when we act.
function onToggleEditShortcut(event) {
    if (!props.active || !canEdit.value) return
    if (event.detail) event.detail.handled = true
    toggleEdit()
}

onMounted(() => {
    window.addEventListener('twicc:toggle-file-edit', onToggleEditShortcut)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:toggle-file-edit', onToggleEditShortcut)
    unregisterCommand(toggleEditCommandId)
})

// Expose the toggle in the command palette only while this is the active,
// editable pane.
watch(
    () => props.active && canEdit.value,
    (available) => {
        if (available) {
            registerCommand({
                id: toggleEditCommandId,
                label: 'Toggle File Edit Mode',
                icon: 'pen-to-square',
                category: 'display',
                toggled: () => isEditing.value,
                action: () => toggleEdit(),
            })
        } else {
            unregisterCommand(toggleEditCommandId)
        }
    },
    { immediate: true },
)

// Entering a preview leaves edit mode (preview and editing are mutually
// exclusive). The preview "eye" is disabled while there are unsaved changes,
// so the buffer is always clean here — no revert needed; the saved content
// simply sits behind the (read-only) preview overlay.
function toggleMarkdownPreview() {
    showMarkdownPreview.value = !showMarkdownPreview.value
    if (showMarkdownPreview.value) isEditing.value = false
}

function toggleSvgPreview() {
    showSvgPreview.value = !showSvgPreview.value
    if (showSvgPreview.value) isEditing.value = false
}

function toggleHtmlPreview() {
    showHtmlPreview.value = !showHtmlPreview.value
    if (showHtmlPreview.value) isEditing.value = false
}

function reloadHtmlPreview() {
    htmlPreviewReloadKey.value++
}

function toggleMermaidPreview() {
    showMermaidPreview.value = !showMermaidPreview.value
    if (showMermaidPreview.value) isEditing.value = false
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

// Expose dirty state, reload, scrollToLine, and loading state for parent components.
// reloadHtmlPreview + isHtmlPreviewActive let the Artifacts tab live-reload a
// rendered HTML page on disk changes.
defineExpose({ isDirty, isLoading, reload, scrollToLine, reloadHtmlPreview, isHtmlPreviewActive })

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
        <!-- File path bar (desktop only, when displayPath is provided). For a
             bookmarkable artifact it also carries the bookmark name (in
             parentheses after the path) and the bookmark toggle (far right). -->
        <template v-if="displayPath">
            <div class="file-path-header">
                <span class="file-path-label" :id="filePathLabelId">{{ displayPath }}<span v-if="artifactBookmark" class="file-path-artifact-bookmark-name"> ({{ artifactBookmark.name }})</span></span>
                <AppTooltip :for="filePathLabelId">{{ displayPath }}<template v-if="artifactBookmark"> ({{ artifactBookmark.name }})</template></AppTooltip>
                <ArtifactBookmarkButton
                    v-if="artifactBookmarkSessionId && isRenderableArtifact && relativeArtifactPath"
                    class="file-path-artifact-bookmark-btn"
                    :session-id="artifactBookmarkSessionId"
                    :relative-path="relativeArtifactPath"
                />
            </div>
            <wa-divider></wa-divider>
        </template>

        <!-- Header toolbar (visible once a file has been loaded) -->
        <div v-if="showHeader && !renderOnly" class="header">
            <div class="header-left">
                <wa-button
                    v-if="!isPreviewing"
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
                <!-- Edit controls: hidden in read-only diff mode (commit diffs),
                     and while a preview is active (editing the source is a
                     separate mode from previewing the rendered result). -->
                <template v-if="(!diffMode || !diffReadOnly) && isWritable && !isPreviewing && !renderOnly">
                    <wa-switch
                        :id="editSwitchId"
                        :checked="isEditing"
                        size="small"
                        @change="onEditToggle"
                    >Edit</wa-switch>
                    <AppTooltip :for="editSwitchId">Toggle edit mode (Alt+E)</AppTooltip>
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
                    v-if="!isPreviewing"
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
                <!-- Markdown preview toggle: shown for .md files, including while
                     editing — but disabled until unsaved changes are saved, since
                     entering the preview leaves edit mode. -->
                <wa-button
                    v-if="isMarkdownFile && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showMarkdownPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="markdownPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMarkdownPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="markdownPreviewButtonId">Toggle markdown preview</AppTooltip>
                <!-- SVG preview toggle: shown for .svg files, including while
                     editing — disabled until unsaved changes are saved. -->
                <wa-button
                    v-if="isSvgFile && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showSvgPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="svgPreviewButtonId"
                    class="reduced-height"
                    @click="toggleSvgPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="svgPreviewButtonId">Toggle SVG preview</AppTooltip>
                <!-- HTML preview toggle: shown for .html files, including while
                     editing (disabled until saved). Excluded in diff mode (Git
                     tab): a preview of the working-tree file would not reflect the
                     diff being viewed. -->
                <wa-button
                    v-if="isHtmlFile && !diffMode && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showHtmlPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="htmlPreviewButtonId"
                    class="reduced-height"
                    @click="toggleHtmlPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="htmlPreviewButtonId">Toggle HTML preview</AppTooltip>
                <!-- Reload the rendered HTML (reflects the file as saved on disk).
                     Only present while the preview is on, which implies not editing. -->
                <wa-button
                    v-if="isHtmlFile && showHtmlPreview && !diffMode"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :id="htmlPreviewReloadButtonId"
                    class="reduced-height"
                    @click="reloadHtmlPreview"
                >
                    <wa-icon name="arrows-rotate"></wa-icon>
                </wa-button>
                <AppTooltip :for="htmlPreviewReloadButtonId">Reload preview</AppTooltip>
                <!-- Mermaid preview toggle: shown for .mmd files, including while
                     editing (disabled until saved). Excluded in diff mode (Git tab). -->
                <wa-button
                    v-if="isMermaidFile && !diffMode && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showMermaidPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="mermaidPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMermaidPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="mermaidPreviewButtonId">Toggle Mermaid preview</AppTooltip>
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

            <!-- HTML preview (when toggled on for .html files). The iframe loads
                 the file from the raw endpoint so the page's relative CSS/JS/asset
                 references resolve to sibling raw URLs. Sandboxed: scripts run but
                 top-level navigation, popups and modals are not allowed. -->
            <iframe
                v-if="showHtmlPreview && isHtmlFile && htmlPreviewSrc && !diffMode"
                :key="filePath"
                :src="htmlPreviewSrc"
                class="html-preview"
                sandbox="allow-scripts allow-same-origin allow-forms"
                title="HTML preview"
            ></iframe>

            <!-- Mermaid preview (when toggled on for .mmd files) — rendered to a
                 pan/zoomable SVG, same in-panel interaction as a rendered image. -->
            <MermaidDiagram
                v-if="showMermaidPreview && isMermaidFile && !diffMode"
                :key="filePath"
                :code="currentContent"
            />

            <!-- PDF preview — the browser's native PDF viewer in an iframe. -->
            <iframe
                v-if="isPdfFile && rawFileUrl && !diffMode"
                :key="filePath"
                :src="rawFileUrl"
                class="pdf-preview"
                title="PDF preview"
            ></iframe>

            <!-- Audio preview — native <audio> player streaming from file-raw. -->
            <div v-if="isAudioFile && rawFileUrl && !diffMode" class="media-preview-container">
                <audio :key="filePath" :src="rawFileUrl" controls class="audio-preview"></audio>
            </div>

            <!-- Video preview — native <video> player streaming from file-raw. -->
            <div v-if="isVideoFile && rawFileUrl && !diffMode" class="media-preview-container">
                <video :key="filePath" :src="rawFileUrl" controls class="video-preview"></video>
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
                v-show="showEditor && !showMarkdownPreview && !showSvgPreview && !showHtmlPreview && !showMermaidPreview && !isBinaryMediaFile"
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
    gap: var(--wa-space-2xs);
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
    flex: 1;
    text-align: center;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Bookmark name shown right after the path, in parentheses. */
.file-path-artifact-bookmark-name {
    color: var(--wa-color-text-normal);
}

.file-path-artifact-bookmark-btn {
    flex-shrink: 0;
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

.html-preview {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
    /* Rendered pages assume an opaque page background; force white so
       transparent/unstyled HTML stays readable in dark mode. */
    background: white;
}

.pdf-preview {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
}

.media-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    display: flex;
    padding: var(--wa-space-m);
}

.audio-preview {
    margin: auto;
    width: min(540px, 100%);
}

.video-preview {
    margin: auto;
    max-width: 100%;
    max-height: 100%;
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
