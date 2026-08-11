// The §7.4 parity contract (agent-sharing design): SAME algorithm as
// src/twicc/core/services/share_url.py, byte-identical output, enforced by
// tests/fixtures/share_url_parity.json. The normative trim set is part of the
// contract — do NOT switch to String.prototype.trim() (its Unicode set
// differs from Python's str.strip()). This module must stay dependency-free:
// shareUrl.test.js imports it under plain `node --test`, where the store's
// extensionless imports do not resolve.
const TRIM_RE = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g

/** Trim the normative ASCII set, then strip trailing slashes. */
export function normalizeShareBase(value) {
    return String(value ?? '').replace(TRIM_RE, '').replace(/\/+$/, '')
}

/** Absolute share URL for a NON-EMPTY stored shareBaseUrl (callers handle
 *  the empty base — the Share UI is disabled without a host). */
export function buildShareUrl(baseValue, urlPath) {
    let base = normalizeShareBase(baseValue)
    if (!base.includes('://')) base = 'https://' + base
    return base + urlPath
}
