<script setup>
import { ref, inject, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { renderMarkdown } from '../../utils/markdown.js'
import { vHighlight } from '../../directives/vHighlight.js'
import { toast } from '../../composables/useToast'
import { openMediaPreview } from '../../composables/useMediaPreview'
// Uses the combined version that includes both light and dark
// Then override with our theme file that uses [data-color-scheme] without media queries
import 'github-markdown-css/github-markdown.css'
import '../../styles/github-markdown-themes.css'

const props = defineProps({
    source: {
        type: String,
        required: true
    },
    showToolbar: {
        type: Boolean,
        default: true
    }
})

const emit = defineEmits(['rendered'])

const router = useRouter()

// Search highlight terms injected from SessionItemsList (empty when no search active)
const highlightTerms = inject('searchHighlightTerms', ref([]))

// File-link resolution context, provided by SessionItemsList. Absent (null)
// when this component is mounted outside a session (e.g., whats-new, tips):
// in that case the post-processing step is skipped and links keep their
// default SPA behavior.
const fileLinks = inject('markdownFileLinks', null)

const renderedHtml = ref('')
const container = ref(null)
const rendering = ref(true)

// Lazy-loaded mermaid instance (dynamic import to avoid ~500KB in main bundle)
let mermaidModule = null
let mermaidInitialized = false

async function getMermaid() {
    if (!mermaidModule) {
        mermaidModule = (await import('mermaid')).default
    }
    if (!mermaidInitialized) {
        mermaidInitialized = true
        mermaidModule.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
            // Don't inject the "Syntax error" bomb diagram on parse/render
            // failures: during streaming, the markdown is re-rendered
            // repeatedly with an incomplete mermaid block, and mermaid would
            // leave one orphan div per failed attempt in <body>, piling up
            // below the app.
            suppressErrorRendering: true,
        })
    }
    return mermaidModule
}

// Render mermaid diagrams found in the parsed HTML
async function renderMermaidDiagrams() {
    if (!container.value) return

    const mermaidBlocks = container.value.querySelectorAll('code.language-mermaid')
    if (mermaidBlocks.length === 0) return

    const mermaid = await getMermaid()

    for (const block of mermaidBlocks) {
        const pre = block.closest('pre')
        if (!pre) continue

        const source = block.textContent
        const id = `mermaid-${Math.random().toString(36).slice(2, 11)}`

        try {
            const { svg } = await mermaid.render(id, source)
            const wrapper = document.createElement('div')
            wrapper.className = 'mermaid-diagram'
            wrapper.innerHTML = svg
            pre.replaceWith(wrapper)
        } catch {
            // If mermaid fails, leave the code block as-is (it will show as plain code)
            pre.classList.add('mermaid-error')
        }
    }
}

// Add data-language attribute to code blocks for the language label
function addLanguageLabels() {
    if (!container.value) return

    for (const pre of container.value.querySelectorAll('pre.shiki')) {
        const code = pre.querySelector('code[class*="language-"]')
        if (!code) continue
        const lang = code.className.match(/language-(\S+)/)?.[1]
        if (lang) pre.dataset.language = lang
    }
}

// Annotate file-path links so handleLinkClick can route them to the Files tab.
// Skips external (http/mailto/...), anchor-only, and Vue Router routes.
function annotateFileLinks() {
    if (!container.value || !fileLinks) return

    for (const a of container.value.querySelectorAll('a')) {
        if (a.getAttribute('target') === '_blank') continue
        const href = a.getAttribute('href')
        if (!href) continue
        if (/^[a-z][a-z0-9+.-]*:/i.test(href)) continue
        if (href.startsWith('#')) continue

        const result = fileLinks.classifyHref(href)
        if (result.kind === 'file') {
            a.setAttribute('data-file-resolved', result.absolutePath)
            if (result.lineNum != null) a.setAttribute('data-file-line', String(result.lineNum))
        } else if (result.kind === 'file-broken') {
            a.setAttribute('data-file-broken', 'true')
            a.removeAttribute('href')
        }
    }
}

async function render() {
    rendering.value = true
    try {
        renderedHtml.value = await renderMarkdown(props.source)
        await nextTick()
        annotateFileLinks()
        addLanguageLabels()
        await renderMermaidDiagrams()
    } finally {
        rendering.value = false
        emit('rendered')
    }
}

watch(() => props.source, render)
onMounted(render)

const showRaw = ref(false)

function toggleRaw() {
    showRaw.value = !showRaw.value
}

function copySource() {
    navigator.clipboard.writeText(props.source)
    toast.success('Markdown copied to clipboard', { duration: 2000 })
}

// Selector for media that should open in MediaPreviewDialog when clicked:
// any <img> rendered from markdown, plus the SVG produced by Mermaid (which
// lives inside .mermaid-diagram). Restricting SVGs to .mermaid-diagram avoids
// hijacking unrelated inline SVGs (e.g. shiki / icon SVGs) that might be
// injected by future plugins.
const MEDIA_SELECTOR = 'img, .mermaid-diagram svg'

// Serialize an inline SVG (e.g. a Mermaid diagram) into a self-contained
// data URL the dialog's <img> can render. We use percent-encoded utf-8
// (data:image/svg+xml;charset=utf-8,...) rather than base64 because it is
// simpler, avoids encoding pitfalls with Unicode, and is universally supported.
//
// Width/height are forced from the viewBox: when an SVG is embedded in
// <img src="data:...">, percentage dimensions (Mermaid emits width="100%")
// have no containing block to resolve against and collapse to 0, making the
// image render as a 0×0 sliver. Explicit pixel dimensions give the <img>
// an intrinsic size the dialog can measure for its fit-content panel.
function svgToDataUrl(svg) {
    const clone = svg.cloneNode(true)
    if (!clone.getAttribute('xmlns')) {
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    }
    const vb = clone.getAttribute('viewBox')
    if (vb) {
        const parts = vb.trim().split(/[\s,]+/).map(Number)
        if (parts.length === 4 && parts.every(Number.isFinite)) {
            const [, , w, h] = parts
            if (w > 0) clone.setAttribute('width', String(w))
            if (h > 0) clone.setAttribute('height', String(h))
        }
    }
    const xml = new XMLSerializer().serializeToString(clone)
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`
}

// Build a MediaItem for an <img>. Walks up to a wrapping <a> (if any) to
// preserve the link as an item-level attribute so the dialog can offer
// "Open link". Skips file-link annotations (data-file-resolved /
// data-file-broken) since those aren't real URLs the user can open.
function buildImgItem(img) {
    if (!img.src) return null
    const anchor = img.closest('a')
    let link = null
    if (anchor
        && !anchor.hasAttribute('data-file-resolved')
        && !anchor.hasAttribute('data-file-broken')) {
        link = anchor.getAttribute('href') || null
    }
    return {
        type: 'image',
        src: img.src,
        name: img.getAttribute('alt') || 'Image',
        link,
    }
}

function buildSvgItem(svg) {
    const src = svgToDataUrl(svg)
    if (!src) return null
    return {
        type: 'image',
        src,
        name: 'Mermaid diagram',
        link: null,
    }
}

// Scan the rendered markdown for every media element (in DOM order) and
// build the items list passed to the preview dialog. Also locates the index
// of the clicked element within the filtered list so navigation lands on it.
function buildMediaItems(root, clickedEl) {
    const nodes = Array.from(root.querySelectorAll(MEDIA_SELECTOR))
    const items = []
    let clickedIndex = 0
    for (const node of nodes) {
        const item = node.tagName === 'IMG' ? buildImgItem(node) : buildSvgItem(node)
        if (!item) continue
        if (node === clickedEl) clickedIndex = items.length
        items.push(item)
    }
    return { items, clickedIndex }
}

// Route clicks on rendered <a> and media (<img>, Mermaid SVG) elements:
//   - <img> or Mermaid SVG → open MediaPreviewDialog (wins over link nav,
//                            but the wrapping <a href>'s URL is preserved
//                            as an "Open link" affordance inside the dialog)
//   - data-file-resolved   → open in Files tab via injected openFile
//   - data-file-broken     → swallow the click (link is rendered as plain text)
//   - external / mailto    → leave the browser to handle (target=_blank or default)
//   - anchor-only (#…)     → leave the browser to scroll
//   - everything else      → SPA navigation via router.push
function handleLinkClick(event) {
    // Image / Mermaid SVG: open the preview dialog and short-circuit. The
    // check runs first so an image wrapped in <a> still opens the dialog
    // (the link remains reachable via the dialog's "Open link" button).
    const media = event.target.closest(MEDIA_SELECTOR)
    if (media && container.value && container.value.contains(media)) {
        event.preventDefault()
        event.stopPropagation()
        const { items, clickedIndex } = buildMediaItems(container.value, media)
        if (items.length > 0) {
            openMediaPreview(items, clickedIndex)
        }
        return
    }

    const anchor = event.target.closest('a')
    if (!anchor) return

    if (anchor.getAttribute('data-file-broken') === 'true') {
        event.preventDefault()
        return
    }

    const fileAbs = anchor.getAttribute('data-file-resolved')
    if (fileAbs) {
        event.preventDefault()
        const lineAttr = anchor.getAttribute('data-file-line')
        const lineNum = lineAttr ? parseInt(lineAttr, 10) : null
        fileLinks?.openFile?.(fileAbs, { lineNum })
        return
    }

    if (anchor.getAttribute('target') === '_blank') return

    const href = anchor.getAttribute('href')
    if (!href) return

    // Skip non-http(s) protocols (mailto:, tel:, etc.)
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^https?:/i.test(href)) return

    // Let the browser scroll for in-page anchors
    if (href.startsWith('#')) return

    // Let the browser handle modifier clicks (open in new tab)
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return

    event.preventDefault()
    router.push(href)
}
</script>

<template>
    <div class="markdown-content-wrapper">
        <div v-if="showToolbar" class="markdown-toolbar">
            <wa-button-group orientation="vertical" label="Markdown tools">
                <wa-button
                    size="small"
                    :variant="showRaw ? 'neutral' : 'brand'"
                    appearance="filled"
                    :title="showRaw ? 'Show rendered markdown' : 'Show raw markdown'"
                    @click="toggleRaw"
                >
                    <wa-icon :name="showRaw ? 'code' : 'eye'"></wa-icon>
                </wa-button>
                <wa-button
                    size="small"
                    variant="neutral"
                    appearance="filled"
                    title="Copy raw markdown"
                    @click="copySource"
                >
                    <wa-icon name="copy"></wa-icon>
                </wa-button>
            </wa-button-group>
        </div>
        <pre v-if="showRaw" class="markdown-raw">{{ source }}</pre>
        <div
            v-show="!showRaw"
            ref="container"
            class="markdown-body"
            v-html="renderedHtml"
            v-highlight="highlightTerms"
            @click="handleLinkClick"
        ></div>
    </div>
</template>

<style>
/* -------------------------------------------------------------------
   Styles NOT covered by github-markdown-css:
   Shiki syntax highlighting extras + Mermaid diagrams.
   Dark mode handled via class data-color-scheme="dark" on <html> (set by main.js).
   NOT scoped — must penetrate v-html content.
   ------------------------------------------------------------------- */
.markdown-content-wrapper {
    position: relative;
}

.markdown-body {
    background: transparent;
    /* Override github-markdown-css fixed 16px to inherit from :root */
    font-size: 1rem;
}

/* -- Floating toolbar (raw toggle + copy) ---------------------------- */
.markdown-toolbar {
    position: absolute;
    top: 0;
    right: 0;
    padding: 0;
    background: transparent;
    border: none;
    opacity: 0;
    transform: scale(0.8);
    transform-origin: top right;
    transition: opacity 0.15s ease;
    z-index: 2;
}
.markdown-content-wrapper:hover .markdown-toolbar {
    opacity: 0.3;
}
.markdown-toolbar:hover {
    opacity: 1 !important;
}
@media (pointer:coarse) {
    .markdown-content-wrapper:focus {
        opacity: 1 !important;
    }
}
.markdown-toolbar wa-button wa-icon {
    width: .6rem;
}

.markdown-raw {
    margin: 0;
    padding: 0;
    background: transparent;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--wa-font-family-body);
    font-size: 1rem;
    color: var(--wa-color-text-normal);
}

/* -- Shiki-generated code blocks ------------------------------------- */
.markdown-body pre {
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    margin-top: 1em;
}
.markdown-body .highlight pre, .markdown-body pre, .markdown-body code, .markdown-body tt {
    font-size: inherit !important;
}
.markdown-body pre.shiki[data-language]:not([data-language="text"]) {
    padding-top: 36px;
    position: relative;
}
.markdown-body pre.shiki[data-language]:not([data-language="text"])::before {
    content: attr(data-language);
    position: absolute;
    top: 8px;
    left: 16px;
    font-size: var(--wa-font-size-s);
    color: #656d76;
    text-transform: uppercase;
    font-family: var(--wa-font-sans);
}

/* -- Mermaid diagrams ------------------------------------------------ */
.markdown-body .mermaid-diagram {
    margin: 16px 0;
    text-align: center;
    overflow-x: auto;
}
.markdown-body .mermaid-diagram svg {
    max-width: 100%;
    height: auto;
}
.markdown-body pre.mermaid-error {
    border-left: 3px solid #d29922;
}
/* Defensive: hide any orphan mermaid temp div that escapes into <body>.
   suppressErrorRendering should prevent this, but a regression in mermaid
   or an unrelated failure path would otherwise stack "Syntax error" bombs
   below the app. */
body > div[id^="dmermaid-"] {
    display: none !important;
}

/* Dark tweak to handle dark mode https://shiki.style/guide/dual-themes */
.shiki, .shiki span {
    --shiki-bg-color: var(--wa-color-surface-default);
    background-color: var(--shiki-bg-color) !important;
}
html.wa-dark .shiki,
html.wa-dark .shiki span {
  color: var(--shiki-dark) !important;
  /* Optional, if you also want font styles */
  font-style: var(--shiki-dark-font-style) !important;
  font-weight: var(--shiki-dark-font-weight) !important;
  text-decoration: var(--shiki-dark-text-decoration) !important;
}

/* -- Search highlight marks (injected by v-highlight directive) ---------- */
mark.search-highlight {
    background-color: oklch(0.85 0.15 90);  /* Warm yellow */
    color: oklch(0.25 0 0);                 /* Dark text for contrast */
    border-radius: 2px;
    padding: 0 1px;
}
html.wa-dark mark.search-highlight {
    background-color: oklch(0.65 0.15 90);  /* Dimmer yellow for dark mode */
    color: oklch(0.95 0 0);                 /* Light text for contrast */
}

/* -- File-path links that don't match any session root render as plain text */
.markdown-body a[data-file-broken] {
    color: inherit;
    text-decoration: none;
    cursor: text;
}

</style>
