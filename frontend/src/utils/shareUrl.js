import { useSettingsStore } from '../stores/settings'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

// Re-export the parity pair so app code keeps one import point; the
// algorithm lives in shareUrlCore.js (dependency-free, node-testable).
export { buildShareUrl, normalizeShareBase }

/** Absolute share URL from a serialized share's url_path, or null when the
 *  `shareBaseUrl` setting is unset (sharing disabled — callers gate the
 *  Share UI on `settings.getShareBaseUrl`). */
export function shareAbsoluteUrl(share) {
    const settings = useSettingsStore()
    const base = normalizeShareBase(settings.getShareBaseUrl)
    if (!base) return null
    return buildShareUrl(base, share.url_path)
}
