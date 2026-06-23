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

// Placeholders allowed in the global "worktree directory template" setting.
// Each is substituted with a value derived from the project the template is
// resolved for. `{project_name}` falls back to the directory leaf when the
// project has no name (same rule as the display name / worktreeLabel);
// `{project_basedir}` is always that leaf. The per-project worktree_directory
// is a plain absolute path and never goes through placeholder expansion.
export const WORKTREE_PLACEHOLDERS = ['git_root', 'project_name', 'project_basedir']

/**
 * Leaf folder name of a POSIX path (e.g. "/home/me/foo/bar" → "bar"). Trailing
 * slashes are ignored. Returns '' for an empty path.
 * @param {string} path
 * @returns {string}
 */
function pathLeaf(path) {
    const p = (path || '').replace(/\/+$/, '')
    const idx = p.lastIndexOf('/')
    return idx >= 0 ? p.slice(idx + 1) : p
}

/**
 * Resolve the placeholder values for a project.
 * @param {{ git_root?: string, directory?: string, name?: string }} project
 * @returns {Record<string, string>}
 */
function placeholderValues(project) {
    const basedir = pathLeaf((project?.directory || '').trim())
    return {
        git_root: (project?.git_root || '').trim(),
        project_basedir: basedir,
        project_name: (project?.name || '').trim() || basedir,
    }
}

/**
 * Validate a worktree directory template's placeholders. Only the names in
 * {@link WORKTREE_PLACEHOLDERS} are allowed; stray "{" / "}" (malformed or
 * unmatched braces) are rejected too. An empty template is valid (= "no
 * default"). Pure and project-independent — placeholder *names* are checked,
 * not whether any given project can satisfy them.
 *
 * @param {string} template
 * @returns {{ valid: boolean, unknown: string[], malformed: boolean }}
 */
export function validateWorktreeTemplate(template) {
    const tpl = template || ''
    const unknown = []
    // Strip every well-formed {name} token, collecting names we don't allow.
    const stripped = tpl.replace(/\{(\w+)\}/g, (match, name) => {
        if (!WORKTREE_PLACEHOLDERS.includes(name)) unknown.push(name)
        return ''
    })
    // Any brace surviving the strip is an unmatched / malformed one.
    const malformed = stripped.includes('{') || stripped.includes('}')
    return { valid: unknown.length === 0 && !malformed, unknown, malformed }
}

/**
 * Expand the global worktree directory template against a project and return
 * the absolute base directory for its worktrees. Placeholders are substituted
 * first, then the result is composed against the project's git root via
 * {@link composeWorktreeDir} (so a template without a leading "/" or
 * "{git_root}" stays relative to the git root, exactly like the legacy relative
 * setting). Returns '' for an empty template.
 *
 * Assumes `template` only contains known placeholders — validate user input
 * with {@link validateWorktreeTemplate} before storing it. Unknown `{tokens}`
 * left in the string are passed through verbatim rather than erased.
 *
 * @param {string} template
 * @param {{ git_root?: string, directory?: string, name?: string }} project
 * @returns {string}
 */
export function expandWorktreeTemplate(template, project) {
    const tpl = (template || '').trim()
    if (!tpl) return ''
    const values = placeholderValues(project)
    const substituted = tpl.replace(/\{(\w+)\}/g, (match, name) =>
        Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match
    )
    return composeWorktreeDir(project?.git_root || '', substituted)
}
