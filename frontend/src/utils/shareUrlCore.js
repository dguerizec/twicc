// Fail-closed Share URL building. The common parser is dependency-free, so
// this module remains usable under plain `node --test`.
import { usablePublicOrigin } from './publicOrigin.js'

/** Trim the normative ASCII set, then strip trailing slashes. */
export function normalizeShareBase(value) {
    return usablePublicOrigin(value)
}

/** Absolute share URL for a NON-EMPTY stored shareBaseUrl (callers handle
 *  the empty base — the Share UI is disabled without a host). */
export function buildShareUrl(baseValue, urlPath) {
    const base = normalizeShareBase(baseValue)
    return base ? base + urlPath : null
}
