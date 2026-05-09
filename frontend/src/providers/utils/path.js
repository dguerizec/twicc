/**
 * Path utilities shared across provider helpers.
 *
 * Originally lived in ``claude_code/toolHelpers.js`` but every provider
 * that surfaces a file path in the tool summary (Codex's ``read`` /
 * ``list_files`` parsed commands, etc.) needs the same logic, so they
 * moved out here.
 */

import { getIconUrl, getFileIconId } from '../../utils/fileIcons'

/**
 * Return ``path`` made relative to ``baseDir`` when it lives under it,
 * else return ``path`` unchanged. Used to keep summaries readable when
 * the agent works inside a known cwd / git root.
 */
export function formatRelativePath(path, baseDir) {
    if (!path) return path
    if (baseDir && path.startsWith(baseDir + '/')) {
        return path.slice(baseDir.length + 1)
    }
    return path
}

/**
 * Collapse ``.`` and ``..`` segments in a slash-separated path.
 * Preserves the leading ``/`` for absolute inputs and keeps any
 * leading ``..`` chain that escapes a relative base. Used by
 * :func:`resolveAbsolutePath` after concatenation; not exported on
 * its own because callers shouldn't need it directly.
 */
function normalizePathSegments(p) {
    const isAbsolute = p.startsWith('/')
    const parts = p.split('/')
    const out = []
    for (const part of parts) {
        if (part === '' || part === '.') continue
        if (part === '..') {
            if (out.length > 0 && out[out.length - 1] !== '..') {
                out.pop()
            } else if (!isAbsolute) {
                out.push('..')
            }
            // At root, ``..`` is a no-op.
        } else {
            out.push(part)
        }
    }
    return (isAbsolute ? '/' : '') + out.join('/')
}

/**
 * Resolve a (possibly) relative ``path`` against ``baseDir``. Mirrors
 * Node's ``path.resolve(baseDir, path)`` for the slice of behaviour
 * we actually need (single base, single relative segment, posix-only):
 *
 *  - ``path`` already absolute (``startsWith('/')``) → returned
 *    normalised (``..`` / ``.`` collapsed) regardless of ``baseDir``.
 *  - ``baseDir`` absolute → joined and normalised, so a parsed
 *    command's ``./src/foo.py`` against an exec_command ``workdir``
 *    becomes the canonical absolute path.
 *  - ``baseDir`` missing or relative → ``path`` returned as-is (still
 *    relative, the caller can decide how to display it).
 */
export function resolveAbsolutePath(path, baseDir) {
    if (!path) return path
    if (path.startsWith('/')) return normalizePathSegments(path)
    if (!baseDir || !baseDir.startsWith('/')) return path
    const joined = baseDir.endsWith('/')
        ? baseDir + path
        : baseDir + '/' + path
    return normalizePathSegments(joined)
}

/**
 * Return a Web Awesome icon URL for the file at ``filePath`` (basename
 * lookup), or ``null`` when the file type is unknown — caller renders
 * no icon in that case rather than the generic ``default-file`` glyph.
 */
export function fileIconFor(filePath) {
    if (!filePath) return null
    const filename = filePath.split('/').pop() || filePath
    const iconId = getFileIconId(filename)
    return iconId !== 'default-file' ? getIconUrl(iconId) : null
}
