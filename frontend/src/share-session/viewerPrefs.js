// Viewer-local share preferences, persisted as a single JSON blob in localStorage
// (one object rather than a key per value, so adding a new viewer pref is just a
// new field — no new key). These are the viewer's own choices on the public share
// page, shared across every share opened on this origin; they are never sent to
// the server. Currently: color scheme + show-timestamps.
const KEY = 'twicc-share-settings'

export function loadViewerPrefs() {
    try {
        const raw = localStorage.getItem(KEY)
        const parsed = raw ? JSON.parse(raw) : null
        return parsed && typeof parsed === 'object' ? parsed : {}
    } catch { return {} }
}

export function saveViewerPref(key, value) {
    try {
        const prefs = loadViewerPrefs()
        prefs[key] = value
        localStorage.setItem(KEY, JSON.stringify(prefs))
    } catch { /* ignore (private mode / disabled storage) */ }
}
