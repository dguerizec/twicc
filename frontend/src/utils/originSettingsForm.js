// Pure per-field preparation for the three origin settings.
// Python owns validation, relationships, and canonical output.

import { checkPublicOriginInput, usablePublicOrigin } from './publicOrigin.js'

export const ORIGIN_SETTING_KEYS = ['publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl']

export function originSettingErrorMessage(errors, field, messageFor) {
    return errors
        .filter(error => error.field === field)
        .map(error => error.message || messageFor(error.code))
        .join(' ')
}

export function refreshOriginInput(currentInput, previousStored, nextStored) {
    return currentInput === (previousStored || '') ? (nextStored || '') : currentInput
}

export function discardOriginSettingWrites(pendingWrites, field) {
    for (const [requestId, write] of pendingWrites) {
        if (write.field === field) pendingWrites.delete(requestId)
    }
}

export function resolveOriginSettingResult(pendingWrites, payload, currentInput) {
    if (!payload || !['accepted', 'rejected'].includes(payload.status)) return null
    const write = pendingWrites.get(payload.request_id)
    if (!write) return null
    pendingWrites.delete(payload.request_id)
    if (currentInput !== write.input) return null
    const value = payload.settings?.[write.field]
    if (payload.status === 'accepted' && typeof value !== 'string') return null
    return {
        field: write.field,
        status: payload.status,
        value: payload.status === 'accepted' ? value : null,
        errors: payload.status === 'rejected' && Array.isArray(payload.errors) ? payload.errors : [],
    }
}

export function validateOriginSetting({ field, input, stored, locationHostname }) {
    if (!ORIGIN_SETTING_KEYS.includes(field)) {
        return { errors: [{ field, code: 'unknown_field' }], warning: null, patch: {} }
    }
    const checked = checkPublicOriginInput(input)
    if (checked.error) {
        return { errors: [{ field, code: checked.error }], warning: null, patch: {} }
    }
    if (field === 'shareBaseUrl' && checked.hostname && locationHostname
            && checked.hostname === locationHostname.toLowerCase()) {
        return { errors: [{ field, code: 'location_hostname' }], warning: null, patch: {} }
    }
    const warning = field === 'peerBaseUrl' && checked.scheme === 'http' ? 'http' : null
    const patch = checked.value === (stored[field] || '') ? {} : { [field]: checked.value }
    const errors = !Object.keys(patch).length && checked.value && !usablePublicOrigin(checked.value)
        ? [{ field, code: 'retained_stored_value' }]
        : []
    return { errors, warning, patch }
}
