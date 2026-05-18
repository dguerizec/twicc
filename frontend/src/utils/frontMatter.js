/**
 * Strip the YAML front-matter block from the start of a markdown string.
 * Used by TipToast.vue : the backend already parsed the front-matter into
 * the manifest, so the frontend just needs the body.
 */
const FM_RE = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/

export function stripFrontMatter(text) {
    return (text || '').replace(FM_RE, '')
}
