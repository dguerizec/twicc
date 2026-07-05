// Message protocol between the browser-companion script (runs inside the
// embedded page, built standalone — see ../../vite.config.companion.js) and
// the BrowserPane host. Shared by both bundles; keep dependency-free.
//
// Envelope: { source, v, type, ...fields }. Unknown types are ignored by both
// sides so the protocol can grow without breaking older companions.

export const COMPANION_SOURCE = 'twicc-browser-companion'
export const HOST_SOURCE = 'twicc-browser-host'
export const PROTOCOL_VERSION = 1

export function companionMessage(type, fields = {}) {
    return { source: COMPANION_SOURCE, v: PROTOCOL_VERSION, type, ...fields }
}

export function hostMessage(type, fields = {}) {
    return { source: HOST_SOURCE, v: PROTOCOL_VERSION, type, ...fields }
}

export function isCompanionMessage(data) {
    return !!data && typeof data === 'object' && data.source === COMPANION_SOURCE && data.v === PROTOCOL_VERSION
}

export function isHostMessage(data) {
    return !!data && typeof data === 'object' && data.source === HOST_SOURCE && data.v === PROTOCOL_VERSION
}
