import { markRaw } from 'vue'

/**
 * Get the parsed content of a session item (or visual item).
 * Parses lazily on first access and caches the result as `_parsedContent`
 * on the item object via markRaw() to prevent Vue deep reactivity.
 *
 * IMPORTANT: This is the ONLY sanctioned way to access parsed content.
 * Never call JSON.parse(item.content) directly — always use this function.
 *
 * @param {Object} item - A session item or visual item with a `.content` string
 * @returns {Object|null} The parsed content object, or null if no content
 */
export function getParsedContent(item) {
    if (item._parsedContent !== undefined) return item._parsedContent
    if (!item.content) return null
    try {
        item._parsedContent = markRaw(JSON.parse(item.content))
    } catch {
        item._parsedContent = markRaw({ error: 'Invalid JSON', raw: item.content })
    }
    return item._parsedContent
}

/**
 * Set the parsed content on an item explicitly.
 * Use this for synthetic items (no raw content string) or to forward
 * a cached parse result to a new object (e.g., visual items).
 *
 * @param {Object} item - The item to set parsed content on
 * @param {Object} parsed - The parsed content object
 */
export function setParsedContent(item, parsed) {
    item._parsedContent = markRaw(parsed)
}

/**
 * Clear the cached parsed content on an item.
 * Use this when the item's raw content string has changed and
 * the cache needs to be invalidated.
 *
 * @param {Object} item - The item to clear parsed content from
 */
export function clearParsedContent(item) {
    delete item._parsedContent
}

/**
 * Check whether an item has content available (loaded or pre-parsed).
 * Use this instead of checking `item.content` directly.
 *
 * Returns true for:
 * - Regular items with a content string (loaded from backend)
 * - Synthetic items with parsed content set via setParsedContent() (no content string)
 *
 * Returns false for:
 * - Metadata-only placeholders (content is null, not yet loaded)
 *
 * @param {Object} item - A session item or visual item
 * @returns {boolean}
 */
export function hasContent(item) {
    return !!(item.content || item._parsedContent !== undefined)
}
