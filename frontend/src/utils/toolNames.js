/**
 * Tool-name display helpers shared between the tool-card shell and
 * per-provider helpers.
 */

/**
 * One name segment → sentence-cased words. Splits both ``snake_case`` and
 * ``camelCase``/``PascalCase`` boundaries, then upper-cases the first letter
 * and lower-cases the rest:
 *   ``foo_bar`` → ``Foo bar``, ``AskUserQuestion`` → ``Ask user question``,
 *   ``WebSearch`` → ``Web search``, ``Read`` → ``Read``.
 */
function humanizeToolSegment(raw) {
    const spaced = raw
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')     // fooBar → foo Bar
        .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')  // HTMLEdit → HTML Edit
        .replace(/_+/g, ' ')
        .trim()
    return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase() : ''
}

/**
 * Fallback header label for tools that have neither a static
 * ``headerLabel`` nor a Task ``displayName``.
 *
 * - Fully-qualified / MCP names (containing ``__`` — Claude Code's
 *   ``mcp__server__tool``, Codex's
 *   ``mcp__chrome_devtools__take_screenshot``) split on ``__``; the ``mcp``
 *   prefix renders as the ``MCP`` acronym, each remaining ``server`` / ``tool``
 *   segment is sentence-cased, and they join with `` : ``:
 *   ``mcp__chrome_devtools__take_screenshot`` → ``MCP : Chrome devtools : Take
 *   screenshot``. Leading / trailing underscores per segment are dropped first
 *   (Codex bare MCP names often start with ``_``).
 * - Every other name (no ``__`` — ``request_user_input``, ``foo_bar``,
 *   ``AskUserQuestion``, ``Read``) is sentence-cased word-by-word →
 *   ``Request user input`` / ``Ask user question`` / ``Read``. So a tool
 *   never surfaces raw (snake_case or PascalCase) in the header without a
 *   per-tool ``getHeaderLabel`` entry.
 */
export function formatToolNameForHeader(rawName) {
    if (typeof rawName !== 'string') return ''
    if (!rawName.includes('__')) {
        return humanizeToolSegment(rawName)
    }
    return rawName
        .split('__')
        .map((s) => s.replace(/^_+|_+$/g, ''))
        .filter(Boolean)
        .map((s) => (s.toLowerCase() === 'mcp' ? 'MCP' : humanizeToolSegment(s)))
        .filter(Boolean)
        .join(' : ')
}
