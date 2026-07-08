import { useSettingsStore } from '../stores/settings'

/** Absolute share URL from a serialized share's url_path. Requires the
 *  `shareBaseUrl` setting — sharing is served only on the dedicated share host and
 *  has no fallback origin (§12). Returns null when it isn't configured; callers gate
 *  the Share UI on `settings.getShareBaseUrl` (empty ⇒ Share entry points disabled). */
export function shareAbsoluteUrl(share) {
    const settings = useSettingsStore()
    const base = (settings.getShareBaseUrl || '').replace(/\/+$/, '')
    if (!base) return null
    return base + share.url_path
}
