// Heuristics to detect and resolve file-path links inside rendered markdown.
//
// Agents frequently emit links like `[file.md](/abs/path/file.md:42)` or
// `[file.md](src/foo.py)` that we want to open in TwiCC's Files tab rather
// than navigate as SPA routes. This module decides, for a given href, whether
// it should be treated as a Vue Router route, a resolvable file path, or a
// file path that doesn't match any known root (rendered as plain text).

const LINE_SUFFIX_RE = /(?::|#L)(\d+)(?:-L?\d+)?$/
const EXTENSION_RE = /\.[a-zA-Z0-9]+$/

/**
 * Strip a trailing `:N` or `#L<N>` line-number suffix from a path, returning the
 * line number. A range suffix (`:N-M`, `#L<N>-L<M>`) is also accepted: only the
 * first number is kept — we reveal a single line, ranges aren't supported.
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
        if (root?.path && absolutePath.startsWith(root.path + '/')) return root
    }
    return null
}

/**
 * Classify a markdown link href.
 *
 * ``roots`` is the ordered descriptor array from utils/projectRoots.js
 * (``{ key, label, path, roles }``), so worktree main-repo roots are honored.
 *
 * Returns one of:
 *   - { kind: 'spa' }
 *       Leave as a regular SPA link (router.push at click time).
 *   - { kind: 'file', candidates, lineNum }
 *       Open one of these absolute paths in the Files tab. ``candidates`` is an
 *       ordered, de-duplicated, non-empty list: deterministic forms (absolute
 *       paths, artifact refs) yield a single entry, while a relative project
 *       path yields one candidate per distinct root (see below). The caller
 *       opens the first candidate that actually exists on disk.
 *   - { kind: 'file-broken', lineNum }
 *       Looks like a file path but does not match any known root; the link
 *       should be rendered as plain text (the caller strips the href).
 *
 * ``artifactsDir`` (optional) is the viewed session's artifacts directory
 * (``<data_dir>/artifacts/<session_id>``, outside the project). When given, an
 * absolute path under it, a relative ``(./)?artifacts/<session_id>/<tail>``, or
 * the URL form ``/artifacts/<session_id>/<tail>`` (the artifact-serving URL
 * path) resolves to it — so the link opens in the Artifacts tab. A plain
 * relative ``artifacts/<other>`` stays project-relative (a real project
 * ``artifacts/`` folder keeps working), since a project tree can't contain a
 * subdir named exactly this session's id.
 *
 * Decision flow:
 *   1. If Vue Router can resolve the href → SPA.
 *   2. Else strip the line suffix and require a file extension; otherwise SPA.
 *   3. Absolute path: artifacts dir first, then the ``/artifacts/<sid>/`` URL
 *      form, then the roots; no match → file-broken.
 *   4. Relative path: the session's artifacts subdir first, else one candidate
 *      per distinct root, in roots order (project / git root first, matching
 *      TwiCC's documented "relative to the project root" convention); if no
 *      root is available, file-broken. A relative path is genuinely ambiguous
 *      when roots nest (a cwd inside the git root): the same link resolves to
 *      a different file under each root, so existence — checked by the caller —
 *      is what disambiguates, not a single up-front guess.
 */
export function classifyHref(href, { router, roots, artifactsDir = null }) {
    if (!href) return { kind: 'spa' }

    // The SPA check only runs for absolute hrefs: Vue Router resolves a
    // relative href against the current route, which can spuriously match
    // a parent route (e.g. `file.md` under `/projects/:id/session/:id`
    // would resolve to a matched parent and look like an SPA route).
    if (href.startsWith('/') && isSpaRoute(href, router)) return { kind: 'spa' }

    const { path, lineNum } = parseLineSuffix(href)
    if (!looksLikeFilePath(path)) return { kind: 'spa' }

    if (path.startsWith('/')) {
        // Artifacts live outside the project roots → check that dir first.
        if (artifactsDir && path.startsWith(artifactsDir + '/')) {
            return { kind: 'file', candidates: [path], lineNum }
        }
        // The `/artifacts/<session_id>/<tail>` URL form — the artifact-serving
        // URL path (e.g. `[x](/artifacts/<sid>/x.html)`) — maps onto the
        // artifacts dir just like the absolute and relative forms.
        if (artifactsDir) {
            const sessionId = artifactsDir.slice(artifactsDir.lastIndexOf('/') + 1)
            const urlPrefix = `/artifacts/${sessionId}/`
            if (path.startsWith(urlPrefix)) {
                return { kind: 'file', candidates: [`${artifactsDir}/${path.slice(urlPrefix.length)}`], lineNum }
            }
        }
        if (findMatchingRoot(path, roots)) return { kind: 'file', candidates: [path], lineNum }
        return { kind: 'file-broken', lineNum }
    }

    // Relative path. A leading "./" is stripped throughout.
    // "artifacts/<this-session-id>/<tail>" is the only relative form a TwiCC
    // artifact reference takes; reinterpret it against the artifacts dir.
    const relative = path.replace(/^\.\//, '')
    if (artifactsDir) {
        const sessionId = artifactsDir.slice(artifactsDir.lastIndexOf('/') + 1)
        const prefix = `artifacts/${sessionId}/`
        if (relative.startsWith(prefix)) {
            return { kind: 'file', candidates: [`${artifactsDir}/${relative.slice(prefix.length)}`], lineNum }
        }
    }

    // Anchor against each root, in roots order (project / git root first), and
    // de-duplicate. When roots nest, "<rel>" under the ancestor and under the
    // descendant are different paths; the caller opens whichever exists.
    const candidates = []
    for (const root of roots) {
        if (!root?.path) continue
        const abs = `${root.path}/${relative}`
        if (!candidates.includes(abs)) candidates.push(abs)
    }
    if (candidates.length === 0) return { kind: 'file-broken', lineNum }
    return { kind: 'file', candidates, lineNum }
}
