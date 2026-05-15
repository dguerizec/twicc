/**
 * Tiny formatting helpers shared between provider tool-helper modules.
 * Kept dependency-free so they can be imported from any helper without
 * dragging in stores or Vue runtime.
 */

/**
 * Replace dashes with spaces and uppercase the first character.
 * Used to turn machine identifiers like ``code-reviewer`` or ``explorer``
 * into header labels (``Code reviewer``, ``Explorer``).
 */
export function capitalize(str) {
    return str.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase())
}
