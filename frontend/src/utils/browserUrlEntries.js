// Saved Browser-pane URL entries — the JS mirror of the backend helpers in
// twicc/workspaces.py. One shared shape for Project.browser_urls and a
// workspace's browserUrls: a list of { url, label?, default? } entries, URLs
// unique within a list, at most one entry flagged default. Every op returns a
// NEW list (entries shallow-copied) so callers can hand it straight to a PUT
// body or the workspaces store.

/** The entry the Home button targets: the flagged one, else the first. */
export function defaultBrowserUrlEntry(entries) {
    if (!Array.isArray(entries) || !entries.length) return null
    return entries.find((e) => e?.default) || entries[0]
}

/**
 * Add `url` to the list (or update it in place — idempotent). The first URL
 * of an empty list always becomes the default; `setDefault` moves the flag.
 * A non-empty `label` overwrites the entry's label.
 */
export function addBrowserUrlEntry(entries, url, { label = null, setDefault = false } = {}) {
    const updated = (entries || []).map((e) => ({ ...e }))
    const makeDefault = setDefault || !updated.length
    let entry = updated.find((e) => e.url === url)
    if (!entry) {
        entry = { url }
        updated.push(entry)
    }
    const trimmedLabel = (label || '').trim()
    if (trimmedLabel) entry.label = trimmedLabel
    if (makeDefault) {
        for (const e of updated) delete e.default
        entry.default = true
    }
    return updated
}

/**
 * Remove `url` from the list (idempotent). The default flag is not
 * re-assigned when the default entry goes — consumers fall back to the
 * first entry when none is flagged.
 */
export function removeBrowserUrlEntry(entries, url) {
    return (entries || []).filter((e) => e.url !== url).map((e) => ({ ...e }))
}

/** Move the default flag to `url`; a URL absent from the list is a no-op. */
export function setDefaultBrowserUrlEntry(entries, url) {
    if (!(entries || []).some((e) => e.url === url)) {
        return (entries || []).map((e) => ({ ...e }))
    }
    return (entries || []).map((e) => {
        const copy = { ...e }
        delete copy.default
        if (copy.url === url) copy.default = true
        return copy
    })
}
