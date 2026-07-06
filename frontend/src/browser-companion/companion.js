// TwiCC browser companion — included by the USER'S OWN page (typically their
// dev server) via a classic <script> tag served at /_twicc/browser-companion.js.
// When that page is embedded in TwiCC's Browser pane, the companion bridges
// the cross-origin gap over postMessage: it reports real navigation (URL
// changes, history capabilities), executes the pane's Back / Forward /
// Reload / Navigate commands against the page's own history, and runs
// host-toggled in-page modes (select-area element outline).
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

    function post(message, targetOrigin) {
        try {
            window.parent.postMessage(message, targetOrigin)
        } catch {
            // Host gone (frame detached mid-flight) — nothing to do.
        }
    }

    function currentState() {
        // Navigation API (Chromium): accurate traversal state. Elsewhere null
        // means "unknown" and the host keeps its buttons enabled.
        const nav = window.navigation
        return {
            url: window.location.href,
            canGoBack: nav ? nav.canGoBack === true : null,
            canGoForward: nav ? nav.canGoForward === true : null,
        }
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
        if (message.action === 'back') {
            window.history.back()
        } else if (message.action === 'forward') {
            window.history.forward()
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

    // SPA URL changes. The History API has no event for pushState/replaceState
    // — patch them; popstate/hashchange cover traversals everywhere, and the
    // Navigation API adds accurate coverage on Chromium. This is the one
    // page-owned global we mutate: keep it perfectly transparent — always call
    // the original, always return its result, and never let our own bookkeeping
    // throw into the page's navigation call.
    for (const method of ['pushState', 'replaceState']) {
        const original = window.history[method]
        window.history[method] = function (...args) {
            const result = original.apply(this, args)
            try {
                scheduleState()
            } catch {
                // Instrumentation must never break the page's navigation.
            }
            return result
        }
    }
    window.addEventListener('popstate', scheduleState)
    window.addEventListener('hashchange', scheduleState)
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
