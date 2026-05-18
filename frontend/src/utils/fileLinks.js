// Heuristics to detect and resolve file-path links inside rendered markdown.
//
// Agents frequently emit links like `[file.md](/abs/path/file.md:42)` or
// `[file.md](src/foo.py)` that we want to open in TwiCC's Files tab rather
// than navigate as SPA routes. This module decides, for a given href, whether
// it should be treated as a Vue Router route, a resolvable file path, or a
// file path that doesn't match any known root (rendered as plain text).

const LINE_SUFFIX_RE = /(?::|#L)(\d+)$/
const EXTENSION_RE = /\.[a-zA-Z0-9]+$/

/**
 * Strip a trailing `:N` or `#L<N>` line-number suffix from a path.
 */
export function parseLineSuffix(href) {
    const m = href.match(LINE_SUFFIX_RE)
    if (!m) return { path: href, lineNum: null }
    return {
        path: href.slice(0, -m[0].length),
        lineNum: parseInt(m[1], 10),
    }
}

/**
 * Heuristic: a string "looks like" a file path only if it ends with an
 * extension `.xxx` where `xxx` is one or more letters/digits.
 */
export function looksLikeFilePath(path) {
    return EXTENSION_RE.test(path)
}

function firstSegment(absoluteHref) {
    const segments = absoluteHref.split('/').filter(Boolean)
    if (segments.length === 0) return ''
    return segments[0].split(/[?#]/)[0]
}

function resolveSilently(href, router) {
    // router.resolve() always logs a "No match found" Vue Router warning when
    // the path doesn't match — but here we use resolve() as a probe to decide
    // whether to claim the href, so any non-match is expected. Swap console.warn
    // for the duration of the (synchronous) resolve call to filter the noise.
    const originalWarn = console.warn
    console.warn = function (msg) {
        if (typeof msg === 'string' && msg.startsWith('[Vue Router warn]')) return
        return originalWarn.apply(this, arguments)
    }
    try {
        return router.resolve(href).matched.length > 0
    } catch {
        return false
    } finally {
        console.warn = originalWarn
    }
}

function isSpaRoute(href, router) {
    // Gate router.resolve() behind a "does the first segment match any known
    // top-level route prefix?" check, so we don't spend time resolving paths
    // like /home/twidi/... that can't possibly be SPA routes.
    const seg = firstSegment(href)
    for (const r of router.getRoutes()) {
        if (firstSegment(r.path) === seg) return resolveSilently(href, router)
    }
    return false
}

function findMatchingRoot(absolutePath, roots) {
    for (const root of roots) {
        if (root && absolutePath.startsWith(root + '/')) return root
    }
    return null
}

/**
 * Classify a markdown link href.
 *
 * Returns one of:
 *   - { kind: 'spa' }
 *       Leave as a regular SPA link (router.push at click time).
 *   - { kind: 'file', absolutePath, lineNum }
 *       Open this absolute path in the Files tab.
 *   - { kind: 'file-broken', lineNum }
 *       Looks like a file path but does not match any known root; the link
 *       should be rendered as plain text (the caller strips the href).
 *
 * Decision flow:
 *   1. If Vue Router can resolve the href → SPA.
 *   2. Else strip the line suffix and require a file extension; otherwise SPA.
 *   3. Absolute path: match against the session roots; no match → file-broken.
 *   4. Relative path: anchor to the first available root (cwd preferred); if
 *      no root is available, file-broken.
 */
export function classifyHref(href, { router, roots }) {
    if (!href) return { kind: 'spa' }

    // The SPA check only runs for absolute hrefs: Vue Router resolves a
    // relative href against the current route, which can spuriously match
    // a parent route (e.g. `file.md` under `/projects/:id/session/:id`
    // would resolve to a matched parent and look like an SPA route).
    if (href.startsWith('/') && isSpaRoute(href, router)) return { kind: 'spa' }

    const { path, lineNum } = parseLineSuffix(href)
    if (!looksLikeFilePath(path)) return { kind: 'spa' }

    if (path.startsWith('/')) {
        const matchingRoot = findMatchingRoot(path, [
            roots.gitDirectory,
            roots.cwd,
            roots.projectDirectory,
            roots.projectGitRoot,
        ])
        if (matchingRoot) return { kind: 'file', absolutePath: path, lineNum }
        return { kind: 'file-broken', lineNum }
    }

    const baseRoot = roots.cwd || roots.projectDirectory || roots.projectGitRoot || roots.gitDirectory
    if (!baseRoot) return { kind: 'file-broken', lineNum }
    return { kind: 'file', absolutePath: `${baseRoot}/${path}`, lineNum }
}
