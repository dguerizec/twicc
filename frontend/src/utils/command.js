/**
 * Extract command information from text that starts with <command-name> XML tags.
 *
 * The CLI writes the command name/message/args verbatim, NOT XML-escaped, so the
 * content can carry bare `&`, `<`, `>` (e.g. `/goal ... echo 'a' && echo 'b'`). A
 * strict XML parser (DOMParser) rejects those as malformed and we'd lose the whole
 * command. Since the structure is fixed and flat, extract each field with a tolerant
 * regex instead — same fields, no choking on unescaped characters.
 *
 * @param {string} text - The text to parse
 * @returns {Object|null} - Command object with name, message, args or null if not a command
 */
export function extractCommand(text) {
    if (typeof text !== 'string') return null

    if (!text.startsWith('<command-')) return null

    const field = (tag) => {
        const match = text.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`))
        return match ? match[1] : null
    }

    const name = field('command-name')
    if (!name) return null

    return {
        name,
        message: field('command-message'),
        args: field('command-args')
    }
}

/**
 * Convert command text to a display-friendly string
 * @param {string} text - The text to convert
 * @returns {string|null} - Display string or null if not a command
 */
export function commandToText(text) {
    const command = extractCommand(text)
    if (!command) return null

    let result = command.name
    if (command.args) {
        result += ` ${command.args}`
    }
    return result
}
