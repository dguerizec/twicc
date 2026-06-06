// frontend/src/utils/worktree.js
// Helpers for displaying git worktree projects (projects whose `worktree_of`
// points at their main repository's project).

/**
 * Label for a worktree project: its own name if it has one, otherwise just the
 * final folder name of its directory (e.g. "/repo/.worktrees/feature-x" →
 * "feature-x"). The full relative/absolute path is intentionally dropped — only
 * the leaf folder is useful in lists.
 *
 * @param {Object} worktree - The worktree project object.
 * @returns {string}
 */
export function worktreeLabel(worktree) {
    if (!worktree) return ''
    if (worktree.name) return worktree.name
    const dir = (worktree.directory || '').replace(/\/+$/, '')
    const idx = dir.lastIndexOf('/')
    return idx >= 0 ? dir.slice(idx + 1) : dir
}
