// Duration presets shared by full-text search, bulk archive, and the sidebar's
// session-list date separators. Order matches display order in lists, from the
// most recent threshold to the oldest.
export const DURATION_PRESETS = [
    { value: '24h', label: '24 hours' },
    { value: '3d',  label: '3 days' },
    { value: '7d',  label: '7 days' },
    { value: '10d', label: '10 days' },
    { value: '20d', label: '20 days' },
    { value: '30d', label: '30 days' },
    { value: '2m',  label: '2 months' },
    { value: '3m',  label: '3 months' },
    { value: '6m',  label: '6 months' },
]

/**
 * Cutoff Date for a preset, relative to `now`. A timestamp is "older than
 * <preset>" when it falls before this cutoff. Returns null for an unknown preset.
 *
 * @param {string} preset - A DURATION_PRESETS value (e.g. '24h', '7d', '3m').
 * @param {Date} now - Reference "now".
 * @returns {?Date}
 */
function presetCutoff(preset, now) {
    const d = new Date(now)
    switch (preset) {
        case '24h': d.setDate(d.getDate() - 1); break
        case '3d':  d.setDate(d.getDate() - 3); break
        case '7d':  d.setDate(d.getDate() - 7); break
        case '10d': d.setDate(d.getDate() - 10); break
        case '20d': d.setDate(d.getDate() - 20); break
        case '30d': d.setDate(d.getDate() - 30); break
        case '2m':  d.setMonth(d.getMonth() - 2); break
        case '3m':  d.setMonth(d.getMonth() - 3); break
        case '6m':  d.setMonth(d.getMonth() - 6); break
        default: return null
    }
    return d
}

/**
 * Convert a duration preset (e.g. '7d', '3m') to an ISO timestamp in the past.
 * Returns null if the preset is unknown.
 */
export function presetToDate(preset) {
    const cutoff = presetCutoff(preset, new Date())
    return cutoff ? cutoff.toISOString() : null
}

/**
 * Bucket a session by the OLDEST preset it is already older than, for the
 * sidebar's date separators. Uses the very same rolling cutoffs as bulk archive,
 * so a separator marks exactly what "Archive sessions older than <preset>" would
 * select.
 *
 * Walks presets newest-first; the bucket is the last one whose cutoff the session
 * predates (once a session is not older than preset P, it cannot be older than
 * any longer preset, so we stop). Sessions newer than the smallest preset (24h)
 * fall in `recent` and get no separator.
 *
 * @param {number} mtime - Last-activity Unix timestamp in seconds.
 * @param {number} nowMs - Reference "now" in epoch milliseconds.
 * @returns {{ key: string, duration: ?string, cutoffMs: ?number }} `duration` is
 *   the preset label (e.g. "24 hours"), `cutoffMs` the exact threshold this
 *   bucket starts at; both null for the `recent` bucket (no separator).
 */
export function sessionPresetBucket(mtime, nowMs) {
    const mtimeMs = mtime * 1000
    const now = new Date(nowMs)
    let bucket = null
    let cutoffMs = null
    for (const preset of DURATION_PRESETS) {
        const c = presetCutoff(preset.value, now).getTime()
        if (mtimeMs < c) {
            bucket = preset
            cutoffMs = c
        } else {
            break
        }
    }
    return bucket
        ? { key: bucket.value, duration: bucket.label, cutoffMs }
        : { key: 'recent', duration: null, cutoffMs: null }
}
