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
