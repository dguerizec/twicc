import { nextTick, onBeforeUnmount, onMounted, watch } from 'vue'
import { debounce } from '../utils/debounce'
import {
    deletePendingRequestDraft,
    getPendingRequestDraft,
    hashPendingRequest,
    savePendingRequestDraft,
} from '../utils/pendingRequestDraftStorage'

// Same cadence as the composer draft (see the data store's _getDebouncedSave).
const SAVE_DELAY = 500

/**
 * Persist and restore the in-progress answer of a pending-request body.
 *
 * A body declares what to snapshot (``collect``) and how to put it back
 * (``apply``); everything else — debounced writes, restore on mount and on
 * request change, deletion once the user answers — happens here. The
 * restoration is silent: nothing is announced, no focus is moved, the controls
 * are simply pre-filled.
 *
 * Deletion is driven by ``isResponding`` rather than by the parent shell: the
 * shell flips it synchronously in ``onBodySubmit`` for EVERY decision (approve,
 * deny, submit, decline, cancel, dismiss), so a single watch here covers every
 * exit path and — crucially — also cancels the debounced write that would
 * otherwise fire after the deletion and resurrect the entry.
 *
 * A draft whose request never comes back (the process restarted, or the user
 * answered from another tab) is collected by the data store's sweep, on the
 * ``active_processes`` snapshot and when a process dies. It can never be
 * mis-applied in the meantime: request ids are unique and never reused, and the
 * stored payload hash is re-checked on restore.
 *
 * @param {Object} options
 * @param {() => string} options.sessionId - Getter for the session id
 * @param {() => Object} options.pendingRequest - Getter for the wire request
 * @param {() => boolean} options.isResponding - Getter for the responding flag
 * @param {() => Object} options.collect - Snapshot the answer state (plain,
 *   JSON-serializable — no Set/Map, no reactive-only values)
 * @param {(state: Object) => void} options.apply - Put a restored state back
 * @param {() => boolean} [options.enabled] - Opt out for requests this body
 *   doesn't actually render itself. A body that delegates some requests to a
 *   self-contained sub-body MUST gate on it: both share the same
 *   (session, request) key, so an ungated parent would overwrite the child's
 *   draft with its own (empty) state.
 */
export function usePendingRequestDraft({
    sessionId,
    pendingRequest,
    isResponding,
    collect,
    apply,
    enabled = () => true,
}) {
    // Bumped on every request change so an in-flight (async) restore for the
    // previous request can never be applied to the new one.
    let generation = 0

    // True from the moment a request takes the slot until its restore has been
    // applied. Writes are suspended meanwhile, which makes this composable
    // independent of watcher ordering: the body's own reset watch wipes the
    // state when a new request arrives, and that wipe must never be persisted
    // — whether it runs before or after our own watchers.
    let settling = true

    const persist = debounce((sid, rid, hash, state) => {
        savePendingRequestDraft(sid, rid, hash, state).catch(err =>
            console.warn('Failed to save pending request draft to IndexedDB:', err)
        )
    }, SAVE_DELAY)

    // The request payload is immutable for the lifetime of a request id, and
    // it can be large (a Write approval carries the whole file content), so the
    // hash is computed once and reused instead of on every keystroke.
    let hashedRequestId = null
    let payloadHash = null

    function requestKey() {
        if (!enabled()) return null
        const request = pendingRequest()
        const rid = request?.request_id
        const sid = sessionId()
        if (!rid || !sid) return null
        if (hashedRequestId !== rid) {
            hashedRequestId = rid
            payloadHash = hashPendingRequest(request)
        }
        return { sid, rid, hash: payloadHash }
    }

    async function restore() {
        const mine = ++generation
        settling = true
        persist.cancel()
        // Let the body's own request_id watch reset its state first, whatever
        // the registration order, so the baseline below is the pristine form.
        await nextTick()
        if (generation !== mine) return
        const key = requestKey()
        const baseline = JSON.stringify(collect())
        try {
            if (key) {
                const state = await getPendingRequestDraft(key.sid, key.rid, key.hash)
                // A newer request took the slot while we were reading — drop it.
                if (state && generation === mine) apply(state)
            }
        } catch (err) {
            console.warn('Failed to read pending request draft from IndexedDB:', err)
        }
        if (generation !== mine) return
        settling = false
        // Catch up on whatever the suspended watch swallowed: the state we just
        // restored, or anything the user typed while the read was in flight.
        if (key && !isResponding()) {
            const current = collect()
            if (JSON.stringify(current) !== baseline) {
                persist(key.sid, key.rid, key.hash, current)
            }
        }
    }

    function discard() {
        persist.cancel()
        const key = requestKey()
        if (!key) return
        deletePendingRequestDraft(key.sid, key.rid).catch(err =>
            console.warn('Failed to delete pending request draft from IndexedDB:', err)
        )
    }

    // Auto-save: ``collect`` builds a fresh object from the body's reactive
    // state, so this watch fires on every edit the user makes. Suspended while
    // a response is in flight (the entry is being deleted, not updated) and
    // while a request is settling into the slot.
    watch(collect, (state) => {
        if (settling || isResponding()) return
        const key = requestKey()
        if (!key) return
        persist(key.sid, key.rid, key.hash, state)
    }, { deep: true })

    // A new request takes over the slot.
    watch(() => pendingRequest()?.request_id, restore)

    // The user answered — whatever the button. Drop the draft.
    watch(isResponding, (responding) => {
        if (responding) discard()
    })

    onMounted(restore)
    onBeforeUnmount(() => persist.cancel())
}
