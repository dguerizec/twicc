/**
 * Tool-name display helpers shared between the tool-card shell and
 * per-provider helpers.
 */

/**
 * Fallback header label for tools that have neither a static
 * ``headerLabel`` nor a Task ``displayName``. Splits on ``__`` (the
 * separator used both by Claude Code's MCP tools ``mcp__server__tool``
 * and by Codex's fully-qualified names like
 * ``mcp__codex_apps__github___search_repositories``), trims any
 * leading / trailing underscores from each segment (Codex bare MCP
 * names often start with ``_``), drops empty segments, and joins back
 * with spaces. Plain tool names without ``__`` pass through unchanged.
 */
export function formatToolNameForHeader(rawName) {
    if (typeof rawName !== 'string') return ''
    return rawName
        .split('__')
        .map((s) => s.replace(/^_+|_+$/g, ''))
        .filter(Boolean)
        .join(' ')
}
