// Duration presets shared by full-text search and bulk archive.
// Order matches display order in lists.
export const DURATION_PRESETS = [
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
 * Convert a duration preset (e.g. '7d', '3m') to an ISO timestamp in the past.
 * Returns null if the preset is unknown.
 */
export function presetToDate(preset) {
    const d = new Date()
    switch (preset) {
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
    return d.toISOString()
}
