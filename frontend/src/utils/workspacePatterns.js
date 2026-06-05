/**
 * Workspace auto-add pattern matching.
 *
 * Must stay in sync with `match_pattern()` in src/twicc/workspaces.py.
 */

/**
 * Check if a directory matches a pattern (`*` = any chars, case insensitive).
 * A pattern without `*` is treated as a directory prefix (`/some/path`
 * behaves like `/some/path/*`).
 *
 * @param {string} directory Absolute project directory.
 * @param {string} pattern Auto-add pattern.
 * @returns {boolean}
 */
export function matchPattern(directory, pattern) {
    const effective = pattern.includes('*') ? pattern : pattern.replace(/\/+$/, '') + '/*'
    const escaped = effective.split('*').map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    return new RegExp('^' + escaped.join('.*') + '$', 'i').test(directory)
}
