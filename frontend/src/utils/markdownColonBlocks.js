// Colon blocks (`:: type label` / `::: type label`) — a markdown-it block
// primitive with two shapes, told apart by the marker length alone. The type
// word is free: it only names the block, it never decides how it parses, so a
// new kind of block needs no change here.
//
//  - **`::` — a line block.** A standalone banner: it owns its line, has no
//    body and no closing marker, and everything after it stays ordinary
//    top-level markdown. Used for the sender header of an inter-session message
//    (src/twicc/cli/_drop_request/sender_header.py).
//
//  - **`:::` (3 or more) — a container block.** Wraps ordinary markdown in a
//    labelled box, code-fence style: an opener, a body, and a closing line of
//    at least as many colons. The body is parsed by the normal block parser, so
//    a fence stays a fence (shiki still highlights it) and a blockquote stays a
//    blockquote — only the wrapper is new. The marker length is variable, so a
//    generator can escape content that itself contains `:::` by opening with a
//    longer run. Used for select-to-comment blocks (stores/codeComments.js).
//
// More colons, bigger structure. Both exist so a generated payload renders as
// its own thing while the source a human reads (in the composer, or in a
// plain-text client) stays light.
//
// Two syntactic guards keep the marker from hijacking ordinary prose:
//  - a space is required after the colon run, so `::before doesn't work` (a CSS
//    pseudo-element opening a line) is left alone;
//  - the label must be non-empty, so a bare `:::` is never an opener — which is
//    also what lets a container's closing line stay unambiguous.
//
// The first word of the label is the type; it becomes a `md-line-<type>` /
// `md-container-<type>` class, slugified (a class is an HTML attribute, and the
// label is arbitrary text).

const COLON = 0x3A /* : */
const SPACE = 0x20
const TAB = 0x09

/** Length of the colon run opening `line`, or 0 when it doesn't open one. */
function colonRunLength(state, line) {
    // 4+ spaces of indent is an indented code block, not a colon block.
    if (state.sCount[line] - state.blkIndent >= 4) return 0
    const start = state.bMarks[line] + state.tShift[line]
    const max = state.eMarks[line]
    if (state.src.charCodeAt(start) !== COLON) return 0
    let pos = start
    while (pos < max && state.src.charCodeAt(pos) === COLON) pos++
    return pos - start
}

/** A CSS-class-safe slug for the type word, or '' when nothing usable is left. */
function slugifyType(word) {
    return word.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '')
}

function colonBlock(state, startLine, endLine, silent) {
    const markerLen = colonRunLength(state, startLine)
    if (markerLen < 2) return false

    const start = state.bMarks[startLine] + state.tShift[startLine]
    const afterMarker = start + markerLen
    const lineEnd = state.eMarks[startLine]
    // A space must follow the run, and a label must follow the space.
    const next = state.src.charCodeAt(afterMarker)
    if (next !== SPACE && next !== TAB) return false
    const info = state.src.slice(afterMarker, lineEnd).trim()
    if (!info) return false

    // Validation pass (paragraph interruption): the opener alone is enough.
    if (silent) return true

    const type = slugifyType(info.split(/\s+/)[0])

    // `::` — one line, nothing else: no body to tokenize, no closing marker to
    // look for, and the rest of the document stays top-level.
    if (markerLen === 2) {
        const lineToken = state.push('colon_line', 'div', 0)
        lineToken.markup = '::'
        lineToken.info = info
        lineToken.meta = { type }
        lineToken.map = [startLine, startLine + 1]
        state.line = startLine + 1
        return true
    }

    // `:::` and longer — find the closing line: a run of >= markerLen colons,
    // alone on its line.
    let nextLine = startLine
    let closed = false
    while (++nextLine < endLine) {
        const runLen = colonRunLength(state, nextLine)
        if (runLen < markerLen) continue
        const lineStart = state.bMarks[nextLine] + state.tShift[nextLine]
        if (state.src.slice(lineStart + runLen, state.eMarks[nextLine]).trim() !== '') continue
        closed = true
        break
    }
    // Unterminated (the block was edited by hand): close at the end of the
    // current scope instead of bailing, so the content never renders as raw
    // `:::` noise. `nextLine` already sits at `endLine` in that case.

    const oldParent = state.parentType
    const oldLineMax = state.lineMax
    state.parentType = 'container'
    state.lineMax = nextLine

    const token = state.push('container_open', 'div', 1)
    token.markup = ':'.repeat(markerLen)
    token.info = info
    token.meta = { type }
    // Spans the whole block (closing line included) so splitMarkdownBlocks slices
    // the container as a single top-level block.
    token.map = [startLine, closed ? nextLine + 1 : nextLine]

    state.md.block.tokenize(state, startLine + 1, nextLine)

    const closeToken = state.push('container_close', 'div', -1)
    closeToken.markup = token.markup

    state.parentType = oldParent
    state.lineMax = oldLineMax
    state.line = closed ? nextLine + 1 : nextLine
    return true
}

/** `"md-container"` plus its `-<type>` modifier when the type slug survived. */
function classAttr(base, type) {
    return type ? `${base} ${base}-${type}` : base
}

/**
 * Register the colon-block rules and renderers on a markdown-it instance.
 * @param {import('markdown-it')} md
 */
export function installColonBlocks(md) {
    // `alt` registers the rule as a paragraph terminator, so an opener on the
    // line right after text starts a block instead of continuing the paragraph
    // (the composer inserts blocks without a leading blank line).
    md.block.ruler.before('fence', 'colon_block', colonBlock, {
        alt: ['paragraph', 'reference', 'blockquote', 'list'],
    })

    // The label is a single line of inline markdown (a file path or a session id
    // arrives as `code`), rendered with the same instance — no async construct
    // can occur inline, so the sync renderer is safe here.
    md.renderer.rules.container_open = (tokens, idx) => {
        const token = tokens[idx]
        const label = `<div class="md-container-label">${md.renderInline(token.info)}</div>`
        return `<div class="${classAttr('md-container', token.meta.type)}">${label}`
    }
    md.renderer.rules.container_close = () => '</div>'

    md.renderer.rules.colon_line = (tokens, idx) => {
        const token = tokens[idx]
        return `<div class="${classAttr('md-line', token.meta.type)}">${md.renderInline(token.info)}</div>`
    }
}
