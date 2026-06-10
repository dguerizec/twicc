// Helpers to compute the base directory for new git worktrees, shared by the
// project-edit dialog (the "use the global default" button) and the
// worktree-create dialog (pre-filling the path field).
//
// These are purely lexical: they never touch the filesystem. The backend
// re-resolves the real path with os.path.realpath when the worktree is
// actually created, so ".." segments left here are only for a clean display.

/**
 * Normalize a POSIX path lexically: drop "." segments, resolve ".." segments,
 * and collapse repeated slashes. Absolute paths keep their leading "/" and
 * ".." cannot climb above the root; relative paths keep leading ".." segments.
 * @param {string} path
 * @returns {string}
 */
export function normalizePosixPath(path) {
    if (!path) return ''
    const isAbsolute = path.startsWith('/')
    const out = []
    for (const segment of path.split('/')) {
        if (!segment || segment === '.') continue
        if (segment === '..') {
            if (out.length && out[out.length - 1] !== '..') {
                out.pop()
            } else if (!isAbsolute) {
                out.push('..')
            }
            // For absolute paths, ".." above the root is simply dropped.
            continue
        }
        out.push(segment)
    }
    return (isAbsolute ? '/' : '') + out.join('/')
}

/**
 * Compose the absolute base directory for a project's worktrees from its git
 * root and a directory expressed RELATIVE to that root (the global
 * "default worktree directory" setting). "../" segments are allowed and
 * resolved. Returns '' when either input is missing/empty.
 *
 * If `relative` is already absolute (defensive — the global setting is meant to
 * be relative), it is returned normalized as-is.
 * @param {string} gitRoot absolute path to the project's git root
 * @param {string} relative directory relative to gitRoot (may contain "../")
 * @returns {string}
 */
export function composeWorktreeDir(gitRoot, relative) {
    const rel = (relative || '').trim()
    if (!rel) return ''
    if (rel.startsWith('/')) return normalizePosixPath(rel)
    const root = (gitRoot || '').replace(/\/+$/, '')
    if (!root) return ''
    return normalizePosixPath(`${root}/${rel}`)
}
