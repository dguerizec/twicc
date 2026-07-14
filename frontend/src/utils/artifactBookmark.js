// Which artifact files can be bookmarked: only the renderable types (everything
// is shown rendered — no source view). Mirrors FilePane's per-type computeds
// (markdown, svg, html, mermaid, pdf, audio, video, images). Used to gate the
// bookmark button on both desktop (FilePane path header) and mobile
// (FileTreePanel header).
const RENDERABLE_ARTIFACT_RE =
    /\.(?:md|markdown|mdown|mkd|mkdn|svg|html?|mmd|mermaid|pdf|mp3|wav|ogg|oga|opus|m4a|aac|flac|weba|mp4|m4v|webm|ogv|mov|png|jpg|jpeg|webp|gif|bmp|ico|avif)$/i

export function isRenderableArtifactPath(path) {
    return !!path && RENDERABLE_ARTIFACT_RE.test(path)
}

// Icon (Font Awesome free, solid) representing the "artifact" concept. Single
// source of truth so it can be swapped in one place.
export const ARTIFACT_ICON = 'shapes'

// File-type icon (Font Awesome free, solid) for an artifact extension.
const EXT_ICON = {
    md: 'file-lines', markdown: 'file-lines',
    html: 'file-code', htm: 'file-code',
    svg: 'file-image', png: 'file-image', jpg: 'file-image', jpeg: 'file-image',
    gif: 'file-image', webp: 'file-image', bmp: 'file-image', ico: 'file-image', avif: 'file-image',
    pdf: 'file-pdf',
    mmd: 'diagram-project', mermaid: 'diagram-project',
    mp3: 'file-audio', wav: 'file-audio', ogg: 'file-audio', oga: 'file-audio',
    opus: 'file-audio', m4a: 'file-audio', aac: 'file-audio', flac: 'file-audio', weba: 'file-audio',
    mp4: 'file-video', m4v: 'file-video', webm: 'file-video', ogv: 'file-video', mov: 'file-video',
}

export function artifactTypeIcon(ext) {
    return EXT_ICON[(ext || '').toLowerCase()] || 'file'
}

export function isHtmlArtifactPath(path) {
    return /\.html?$/i.test(path || '')
}

// Turn a path segment into a human title: drop the extension, split on
// separators, collapse whitespace, Title Case. "sales-report.svg" → "Sales
// Report", "dashboard.html" → "Dashboard".
function humanizeSegment(segment) {
    const base = (segment || '').replace(/\.[^.]+$/, '') || segment || ''
    const words = base.replace(/[-_.]+/g, ' ').replace(/\s+/g, ' ').trim()
    if (!words) return ''
    return words.replace(/\b\w/g, (c) => c.toUpperCase())
}

// Derive a name from the file path alone (no content). An index.html borrows
// its parent folder name (that's the artifact's real identity); anything else
// uses its own basename. Both are humanized.
function deriveNameFromPath(relativePath) {
    const parts = String(relativePath || '').split('/').filter(Boolean)
    if (!parts.length) return ''
    const base = parts[parts.length - 1]
    if (/^index\.html?$/i.test(base) && parts.length >= 2) {
        return humanizeSegment(parts[parts.length - 2])
    }
    return humanizeSegment(base)
}

// Extract a title from HTML content: <title> first, then the first <h1>. Used
// verbatim (whitespace-collapsed) — it's already a human-authored title, no
// humanization. Returns '' when neither is present or non-empty. DOMParser runs
// no scripts and loads no resources, so parsing arbitrary artifact HTML is safe.
function extractHtmlTitle(htmlContent) {
    if (!htmlContent) return ''
    let doc
    try {
        doc = new DOMParser().parseFromString(htmlContent, 'text/html')
    } catch {
        return ''
    }
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim()
    return clean(doc.querySelector('title')?.textContent) || clean(doc.querySelector('h1')?.textContent) || ''
}

// Suggest a default bookmark name. For HTML with content available, prefer its
// <title>/<h1>; otherwise (non-HTML, or HTML without a usable title) derive a
// humanized name from the path. Synchronous and pure — callers fetch the HTML
// and pass it in. Truncated to the model's name max_length (255).
export function suggestArtifactBookmarkName({ relativePath, htmlContent = null } = {}) {
    if (!relativePath) return ''
    let name = ''
    if (isHtmlArtifactPath(relativePath) && htmlContent) {
        name = extractHtmlTitle(htmlContent)
    }
    if (!name) name = deriveNameFromPath(relativePath)
    return name.slice(0, 255).trim()
}
