const CHANNEL_NAME = 'twicc-draft-messages-v1'
const UPDATE_TYPE = 'draft-message-committed'

function normalizeUpdate(value) {
    if (
        !value
        || value.type !== UPDATE_TYPE
        || typeof value.sessionId !== 'string'
        || !value.sessionId
        || !value.draft
        || typeof value.draft !== 'object'
        || typeof value.draft.message !== 'string'
    ) {
        return null
    }
    return {
        sessionId: value.sessionId,
        draft: value.draft,
    }
}

/**
 * Create the small same-origin channel used to hand a durably persisted draft
 * to another TwiCC page. A host may route to an already-running page whose
 * Pinia store will not re-hydrate IndexedDB on its own.
 *
 * @param {typeof BroadcastChannel|undefined} BroadcastChannelImpl
 */
export function createDraftMessageSync(BroadcastChannelImpl) {
    let channel = null
    const listeners = new Set()

    function ensureChannel() {
        if (!channel && typeof BroadcastChannelImpl === 'function') {
            channel = new BroadcastChannelImpl(CHANNEL_NAME)
            channel.addEventListener('message', (event) => {
                const update = normalizeUpdate(event?.data)
                if (!update) return
                for (const listener of listeners) listener(update)
            })
        }
        return channel
    }

    return {
        subscribe(listener) {
            if (typeof listener !== 'function' || !ensureChannel()) return () => {}
            listeners.add(listener)
            return () => listeners.delete(listener)
        },

        publish(sessionId, draft) {
            if (!ensureChannel()) return false
            channel.postMessage({ type: UPDATE_TYPE, sessionId, draft })
            return true
        },

        close() {
            listeners.clear()
            channel?.close()
            channel = null
        },
    }
}

let browserSync = null

function getBrowserSync() {
    if (!browserSync) {
        const BroadcastChannelImpl = typeof window === 'undefined'
            ? undefined
            : window.BroadcastChannel
        browserSync = createDraftMessageSync(BroadcastChannelImpl)
    }
    return browserSync
}

export function subscribeToDraftMessageCommits(listener) {
    return getBrowserSync().subscribe(listener)
}

export function publishDraftMessageCommit(sessionId, draft) {
    return getBrowserSync().publish(sessionId, draft)
}

export function appendDraftMessageText(existingMessage, appendedMessage) {
    const existing = typeof existingMessage === 'string' ? existingMessage.trim() : ''
    return existing ? `${existing}\n\n${appendedMessage}` : appendedMessage
}

/**
 * Decide whether a store update can safely replace the text currently shown
 * by a mounted composer. Empty composers accept hydration; non-empty ones only
 * accept the explicit append shape used by programmatic handoffs, so a stale
 * IndexedDB hydration cannot overwrite unrelated user typing.
 */
export function shouldApplyDraftMessageUpdate(currentMessage, nextMessage) {
    if (typeof nextMessage !== 'string' || !nextMessage) return false
    const current = typeof currentMessage === 'string' ? currentMessage.trim() : ''
    return !current || nextMessage.startsWith(`${current}\n\n`)
}

export function replaceDraftMessageText(draft, message) {
    const existing = draft && typeof draft === 'object' ? draft : {}
    return { ...existing, message }
}

export function appendToDraftRecord(persistedDraft, localDraft, appendedMessage) {
    const persisted = persistedDraft && typeof persistedDraft === 'object' ? persistedDraft : {}
    const local = localDraft && typeof localDraft === 'object' ? localDraft : {}
    return {
        ...persisted,
        message: appendDraftMessageText(
            local.message ?? persisted.message,
            appendedMessage,
        ),
    }
}
