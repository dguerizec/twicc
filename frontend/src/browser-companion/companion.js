// TwiCC browser companion — included by the USER'S OWN page (typically their
// dev server) via a classic <script> tag served at /_twicc/browser-companion.js.
// When that page is embedded in TwiCC's Browser pane, the companion bridges
// the cross-origin gap over postMessage: it reports real navigation (URL
// changes, history capabilities) and executes the pane's Back / Forward /
// Reload / Navigate commands against the page's own history.
//
// Trust model: the embedder is unknown at load time, so the initial `hello`
// is payload-free and posted to '*'. The page URL only flows AFTER the host
// acks, targeted at the acked origin; commands are only honoured from it.
// Loaded outside a frame (or twice), the script does nothing.
import { companionMessage, isHostMessage } from './protocol'

if (window.parent !== window && !window.__twiccBrowserCompanion) {
    window.__twiccBrowserCompanion = true
    install()
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
        }
    })

    // SPA URL changes. The History API has no event for pushState/replaceState
    // — patch them; popstate/hashchange cover traversals everywhere, and the
    // Navigation API adds accurate coverage on Chromium.
    for (const method of ['pushState', 'replaceState']) {
        const original = window.history[method]
        window.history[method] = function (...args) {
            const result = original.apply(this, args)
            scheduleState()
            return result
        }
    }
    window.addEventListener('popstate', scheduleState)
    window.addEventListener('hashchange', scheduleState)
    window.navigation?.addEventListener('currententrychange', scheduleState)

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
