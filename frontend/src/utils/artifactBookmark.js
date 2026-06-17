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
