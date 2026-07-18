// TwiCC browser companion — included by the USER'S OWN page (typically their
// dev server) via a classic <script> tag served at /_twicc/browser-companion.js.
// When that page is embedded in TwiCC's Browser pane, the companion bridges
// the cross-origin gap over postMessage: it reports real navigation (URL
// changes), moves the page to a host-supplied target URL (soft in-document when
// possible, else a reload — it NEVER traverses the tab's shared session history,
// so the TwiCC parent can't be dragged along), reloads it, and runs host-toggled
// in-page modes (select-area element outline).
//
// Trust model: the embedder is unknown at load time, so the initial `hello`
// is payload-free and posted to '*'. The page URL only flows AFTER the host
// acks, targeted at the acked origin; commands are only honoured from it.
// Loaded outside a frame (or twice), the script does nothing.
import { createElementPicker } from '../element-select/picker'
import { companionMessage, isHostMessage } from './protocol'

// This script is injected into a page we do not own. It must be INVISIBLE to
// that page: everything lives inside install()'s closure (the Vite IIFE build
// exposes no module global), the single global property we set is namespaced
// (window.__twiccBrowserCompanion, the double-load guard), and a failure must
// never surface in the host — a throw here just leaves the companion "absent".
if (window.parent !== window && !window.__twiccBrowserCompanion) {
    window.__twiccBrowserCompanion = true
    try {
        install()
    } catch {
        // Swallow: the host page must never see a companion error.
    }
}

function install() {
    let hostOrigin = null
    let stateScheduled = false

    // Current URL, with a bare trailing '#' normalised away: clearing a fragment
    // (location.hash = '') can leave one behind in some engines, and it must
    // compare equal to the fragmentless URL for the stack bookkeeping below.
    function href() {
        const h = window.location.href
        return h.endsWith('#') ? h.slice(0, -1) : h
    }

    // ── Current-document navigation memory. The HOST owns the Back/Forward
    // history (it lives in the TwiCC parent, which — unlike this frame — is never
    // reloaded, so it survives full page navigations, MPA and cross-origin
    // alike). The companion only remembers the URLs reachable WITHOUT a reload
    // inside the CURRENT document (soft SPA routes), mapped to the page's
    // history.state so a soft move can restore it. Rebuilt from scratch on every
    // document load — exactly the set that is soft-reachable right now.
    const LIVE_DOC_MAX = 100
    const liveDoc = new Map([[href(), window.history.state]])

    function post(message, targetOrigin) {
        try {
            window.parent.postMessage(message, targetOrigin)
        } catch {
            // Host gone (frame detached mid-flight) — nothing to do.
        }
    }

    function currentState() {
        // Just the URL. Back/Forward capability is the host's to compute — it
        // owns the cross-document history; the companion only reports where the
        // page currently is.
        return { url: href() }
    }

    // Coalesce bursts: a pushState patched below AND the Navigation API both
    // fire for one SPA navigation.
    function scheduleState() {
        if (!hostOrigin || stateScheduled) return
        stateScheduled = true
        queueMicrotask(() => {
            stateScheduled = false
            if (hostOrigin) post(companionMessage('state', currentState()), hostOrigin)
        })
    }

    // ── Navigating the page WITHOUT traversing the shared session history ─────
    // A browser keeps ONE session history for the whole tab: every entry is a
    // snapshot of the top document (TwiCC) AND every frame. window.history.back()
    // / forward() — whoever calls them — merely step that shared pointer by one,
    // so from an embedded frame a step can land on an entry that differs only in
    // the TOP frame and navigate TwiCC itself instead of this page (the
    // frame-scoped Navigation API back()/forward() fail the same way once a
    // parent navigation is interleaved). So the companion NEVER traverses.
    //
    // Instead it goes to a host-supplied target URL by ADDING a navigation — a
    // frame-local act that can never move the parent. If the target is reachable
    // inside the current document (a soft SPA route we have seen), it moves there
    // softly like the SPA itself would: pushState (or a hash change) plus a
    // signal to the page's router (a synthetic popstate, or the native
    // hashchange), no reload. Otherwise the target lives in another document, so
    // it is a plain location.assign — a reload of that page, exactly as a real
    // browser Back across documents is. Either way the parent is untouched.
    function sameExceptHash(a, b) {
        const ai = a.indexOf('#')
        const bi = b.indexOf('#')
        return (ai === -1 ? a : a.slice(0, ai)) === (bi === -1 ? b : b.slice(0, bi))
    }

    function recordLive() {
        // Remember the current URL as soft-reachable in this document, capped so
        // a long-lived SPA can't grow it without bound (evicting the oldest only
        // downgrades a very old route's Back to a reload).
        liveDoc.set(href(), window.history.state)
        if (liveDoc.size > LIVE_DOC_MAX) liveDoc.delete(liveDoc.keys().next().value)
    }

    function softNavigate(url, state) {
        try {
            if (sameExceptHash(url, href())) {
                // Hash-only change: a hashchange-based router reacts only to a
                // real hash change (a synthetic popstate would not fire one).
                const hi = url.indexOf('#')
                window.location.hash = hi === -1 ? '' : url.slice(hi)
            } else {
                window.history.pushState(state ?? null, '', url)
                window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
            }
        } catch {
            // A refused navigation (e.g. a malformed url) — do nothing.
        }
        scheduleState()
    }

    function navigateTo(url) {
        // The host's Back/Forward resolve to a target URL. Soft if we can reach
        // it in this document without a reload; a reload of that page otherwise.
        if (url === href()) return
        if (liveDoc.has(url)) softNavigate(url, liveDoc.get(url))
        else window.location.assign(url)
    }

    window.addEventListener('message', (event) => {
        if (event.source !== window.parent || !isHostMessage(event.data)) return
        const message = event.data
        if (message.type === 'ack') {
            hostOrigin = event.origin
            scheduleState()
            // Flush any errors captured before the host connected.
            if (errorLog.length) scheduleErrorPost()
            return
        }
        if (message.type !== 'command' || event.origin !== hostOrigin) return
        if (message.action === 'navigate-to' && typeof message.url === 'string' && /^https?:\/\//i.test(message.url)) {
            navigateTo(message.url)
        } else if (message.action === 'reload') {
            window.location.reload()
        } else if (message.action === 'navigate' && typeof message.url === 'string' && /^https?:\/\//i.test(message.url)) {
            window.location.assign(message.url)
        } else if (message.action === 'select-mode') {
            if (message.enabled === true) picker.enable()
            else picker.disable()
        } else if (message.action === 'select-nav') {
            picker.nav(message.direction)
        } else if (message.action === 'select-clear') {
            picker.clear()
        } else if (message.action === 'select-describe') {
            postSelectDescribe()
        } else if (message.action === 'select-capture') {
            postSelectCapture()
        }
    })

    // ── Select-area mode: the host-toggled element picker, extracted to the
    // shared element-select/picker.js (the SPA runs the same picker directly
    // against the artifact HTML preview). Here it runs inside the user's own
    // page; this section is the postMessage adapter around it.
    const picker = createElementPicker({
        win: window,
        doc: document,
        onState: (state) => {
            if (hostOrigin) post(companionMessage('select-state', state), hostOrigin)
        },
    })

    function postSelectDescribe() {
        if (!hostOrigin) return
        const description = picker.describe()
        if (!description) return
        post(companionMessage('select-describe', description), hostOrigin)
    }

    // Always answers (success or error), so the host's pending capture state
    // can't get stuck.
    async function postSelectCapture() {
        if (!hostOrigin) return
        try {
            const dataUrl = await picker.capture()
            post(companionMessage('select-capture', { dataUrl }), hostOrigin)
        } catch (error) {
            post(companionMessage('select-capture', { error: String(error?.message || error) }), hostOrigin)
        }
    }

    // SPA URL changes. The History API fires no event for pushState/replaceState
    // — patch them so a soft in-document route is remembered (and reported). Stay
    // perfectly transparent: always call the original, always return its result,
    // never let our bookkeeping throw into the page's navigation call.
    for (const method of ['pushState', 'replaceState']) {
        const original = window.history[method]
        window.history[method] = function (...args) {
            const result = original.apply(this, args)
            try {
                recordLive()
                scheduleState()
            } catch {
                // Instrumentation must never break the page's navigation.
            }
            return result
        }
    }
    // popstate / hashchange: the page's URL changed under its own steam (a link,
    // its router, its own Back button) — or our own soft nav, which lands on a
    // URL already in liveDoc, so recording it again is a harmless no-op.
    window.addEventListener('popstate', () => {
        recordLive()
        scheduleState()
    })
    window.addEventListener('hashchange', () => {
        recordLive()
        scheduleState()
    })
    window.navigation?.addEventListener('currententrychange', scheduleState)

    // ── Page error capture: console.error, uncaught exceptions and unhandled
    // rejections are buffered and the list pushed to the host, which badges the
    // count and lets the user hand them to the agent. Wrapping console.error is
    // the one page global we touch beyond the history patch — kept transparent:
    // we always delegate to the original and never throw into the caller. The
    // listeners are passive observers (no preventDefault), so the page's own
    // error handling is untouched.
    const ERROR_LOG_MAX = 50
    const ERROR_TEXT_MAX = 1000
    const errorLog = []
    let errorTotal = 0
    let errorPostScheduled = false

    function formatErrorArg(arg) {
        if (arg instanceof Error) return arg.stack || `${arg.name}: ${arg.message}`
        if (typeof arg === 'string') return arg
        try {
            return JSON.stringify(arg)
        } catch {
            try {
                return String(arg)
            } catch {
                return '[unserializable]'
            }
        }
    }

    function scheduleErrorPost() {
        if (!hostOrigin || errorPostScheduled) return
        errorPostScheduled = true
        // Coalesce a synchronous burst (one render throwing many errors) into a
        // single post carrying the whole (capped) buffer.
        queueMicrotask(() => {
            errorPostScheduled = false
            if (hostOrigin) post(companionMessage('errors', { errors: errorLog.slice(), total: errorTotal }), hostOrigin)
        })
    }

    function pushError(kind, text) {
        const trimmed = (text || '').trim()
        if (!trimmed) return
        errorTotal++
        errorLog.push({ kind, text: trimmed.length > ERROR_TEXT_MAX ? `${trimmed.slice(0, ERROR_TEXT_MAX)}…` : trimmed })
        if (errorLog.length > ERROR_LOG_MAX) errorLog.shift()
        scheduleErrorPost()
    }

    const originalConsoleError = console.error
    console.error = function (...args) {
        try {
            pushError('console.error', args.map(formatErrorArg).join(' '))
        } catch {
            // Capture must never disturb the page's own logging.
        }
        return originalConsoleError.apply(this, args)
    }

    window.addEventListener('error', (event) => {
        // Uncaught JS exceptions only: an ErrorEvent carries a message. Resource
        // load failures (img/script) also fire here but with an element target
        // and no message — skip those.
        if (!event.message) return
        const where = event.filename ? ` (${event.filename}:${event.lineno}:${event.colno})` : ''
        pushError('uncaught', event.error?.stack || `${event.message}${where}`)
    })

    window.addEventListener('unhandledrejection', (event) => {
        pushError('unhandledrejection', formatErrorArg(event.reason))
    })

    // User interactions inside the page never reach the host document (the
    // frame is cross-origin), so the host's click-to-focus rule — interacting
    // with a pane claims it — can't see them. Report real input events
    // instead. Deliberately NOT the window focus event: a page can focus
    // itself with no user gesture (window.focus() on load), which would let a
    // background pane steal the host's active tab. Throttled so a typing
    // burst doesn't flood the channel — one claim a second is plenty.
    let lastFocusPost = 0
    function reportInteraction() {
        if (!hostOrigin) return
        const now = Date.now()
        if (now - lastFocusPost < 1000) return
        lastFocusPost = now
        post(companionMessage('focus'), hostOrigin)
    }
    window.addEventListener('pointerdown', reportInteraction, true)
    window.addEventListener('keydown', reportInteraction, true)

    // Distinguish "navigating away" from "companion never present": the host
    // flips to 'waiting' on bye and only declares absence after a post-load
    // grace period with no hello.
    window.addEventListener('pagehide', () => {
        if (hostOrigin) post(companionMessage('bye'), hostOrigin)
    })

    // bfcache restore: the host may have been remounted meanwhile — redo the
    // handshake from scratch.
    window.addEventListener('pageshow', (event) => {
        if (!event.persisted) return
        hostOrigin = null
        post(companionMessage('hello'), '*')
    })

    post(companionMessage('hello'), '*')
}
