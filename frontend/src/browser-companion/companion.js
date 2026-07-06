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
import { domToPng } from 'modern-screenshot'

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
            setSelectMode(message.enabled === true)
        } else if (message.action === 'select-nav') {
            selectNav(message.direction)
        } else if (message.action === 'select-clear') {
            selectClear()
        } else if (message.action === 'select-describe') {
            postSelectDescribe()
        } else if (message.action === 'select-capture') {
            postSelectCapture()
        }
    })

    // ── Select-area mode: a host-toggled element picker. A full-viewport
    // overlay swallows every pointer interaction — the page must not react at
    // all while the mode is on — and the element under the pointer gets a
    // dashed outline. Two states: hovering (red outline follows the pointer)
    // and locked (a click/tap turned it green; hover is ignored so the user
    // can travel to the host's toolbar without losing the selection — toolbar
    // navigation and clear both keep/lift the lock). The outline box is a
    // separate pointer-events:none node, so hit-testing skips it;
    // elementsFromPoint sees the page through the overlay by filtering our own
    // two nodes out.
    let selectOverlay = null
    let selectOutline = null
    let selectLastPoint = null
    let selectCurrent = null
    let selectLocked = false

    function isOwnSelectNode(node) {
        return node === selectOverlay || node === selectOutline
    }

    // One DOM step from `el`, skipping our own overlay/outline nodes (both are
    // children of <body>, so a sibling walk there would land on them).
    function selectStepFrom(el, direction) {
        if (direction === 'parent') return el.parentElement
        let node =
            direction === 'first-child'
                ? el.firstElementChild
                : direction === 'prev-sibling'
                  ? el.previousElementSibling
                  : direction === 'next-sibling'
                    ? el.nextElementSibling
                    : null
        while (node && isOwnSelectNode(node)) {
            node = direction === 'prev-sibling' ? node.previousElementSibling : node.nextElementSibling
        }
        return node
    }

    // Where the host's toolbar buttons can go from the current element — sent
    // on every element change so the buttons enable/disable live.
    function postSelectState() {
        if (!hostOrigin) return
        const el = selectCurrent
        post(
            companionMessage('select-state', {
                hasSelection: !!el,
                locked: selectLocked,
                canParent: !!(el && selectStepFrom(el, 'parent')),
                canFirstChild: !!(el && selectStepFrom(el, 'first-child')),
                canPrevSibling: !!(el && selectStepFrom(el, 'prev-sibling')),
                canNextSibling: !!(el && selectStepFrom(el, 'next-sibling')),
            }),
            hostOrigin
        )
    }

    function drawSelectOutline() {
        if (!selectOutline) return
        if (selectCurrent && !selectCurrent.isConnected) {
            // The page re-rendered under us (SPA) — the selection is gone.
            setSelectCurrent(null, false)
            return
        }
        if (!selectCurrent) {
            selectOutline.style.display = 'none'
            return
        }
        const rect = selectCurrent.getBoundingClientRect()
        selectOutline.style.outlineColor = selectLocked ? '#30a46c' : '#e5484d'
        selectOutline.style.display = 'block'
        selectOutline.style.left = `${rect.left}px`
        selectOutline.style.top = `${rect.top}px`
        selectOutline.style.width = `${rect.width}px`
        selectOutline.style.height = `${rect.height}px`
    }

    function setSelectCurrent(el, locked) {
        const changed = el !== selectCurrent || locked !== selectLocked
        selectCurrent = el
        selectLocked = locked
        drawSelectOutline()
        if (changed) postSelectState()
    }

    function selectNav(direction) {
        if (!selectOverlay || !selectCurrent) return
        const next = selectStepFrom(selectCurrent, direction)
        if (!next) return
        // An explicit choice locks the selection (green), whatever it started
        // from, and drops the pointer anchor (the scrollIntoView below would
        // otherwise immediately re-aim at the stale pointer position).
        selectLastPoint = null
        setSelectCurrent(next, true)
        next.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }

    function selectClear() {
        selectLastPoint = null
        setSelectCurrent(null, false)
    }

    function highlightAt(x, y) {
        selectLastPoint = { x, y }
        const el = document.elementsFromPoint(x, y).find((node) => !isOwnSelectNode(node))
        setSelectCurrent(el || null, false)
    }

    // Human/agent-readable description of the current element: a CSS-selector
    // style chain of ancestors plus the element's opening tag and visible
    // text. Unlike an indexed XPath, this maps onto what the page's SOURCE
    // looks like — ids, classes and text are what an agent can grep for.
    const SELECT_MAX_CLASSES = 4
    const SELECT_MAX_TEXT = 100
    const SELECT_MAX_TAG = 300

    // One chain segment: tag#id.classes, with :nth-of-type only when the
    // segment alone would be ambiguous among same-tag siblings.
    function selectSegmentFor(node) {
        const signature = (el) =>
            `${el.id}|${[...el.classList].slice(0, SELECT_MAX_CLASSES).join('.')}`
        let segment = node.localName
        if (node.id) segment += `#${node.id}`
        const classes = [...node.classList].slice(0, SELECT_MAX_CLASSES)
        if (classes.length) segment += `.${classes.join('.')}`
        const sameTag = node.parentElement
            ? [...node.parentElement.children].filter(
                  (sib) => !isOwnSelectNode(sib) && sib.localName === node.localName
              )
            : []
        if (sameTag.length > 1 && sameTag.filter((sib) => signature(sib) === signature(node)).length > 1) {
            segment += `:nth-of-type(${sameTag.indexOf(node) + 1})`
        }
        return segment
    }

    function selectChainFor(el) {
        const parts = []
        for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
            parts.unshift(selectSegmentFor(node))
        }
        return parts.length ? parts.join(' > ') : el.localName
    }

    // The element's own opening tag — it carries every attribute (id, classes,
    // data-*, aria-*) in the exact shape the source declares them. Vue's
    // scoped-style data-v-* markers are compile-time noise and are dropped.
    function selectOpeningTagFor(el) {
        const clone = el.cloneNode(false)
        for (const name of clone.getAttributeNames()) {
            if (name.startsWith('data-v-')) clone.removeAttribute(name)
        }
        const html = clone.outerHTML
        const end = html.indexOf('>')
        const tag = end === -1 ? html : html.slice(0, end + 1)
        return tag.length > SELECT_MAX_TAG ? `${tag.slice(0, SELECT_MAX_TAG)}…>` : tag
    }

    function selectTextFor(el) {
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim()
        return text.length > SELECT_MAX_TEXT ? `${text.slice(0, SELECT_MAX_TEXT)}…` : text
    }

    function postSelectDescribe() {
        if (!hostOrigin || !selectCurrent) return
        post(
            companionMessage('select-describe', {
                chain: selectChainFor(selectCurrent),
                openingTag: selectOpeningTagFor(selectCurrent),
                text: selectTextFor(selectCurrent),
            }),
            hostOrigin
        )
    }

    // Render the current element to a PNG data URL with modern-screenshot (a
    // live-DOM-to-image renderer, foreignObject-based). Fidelity is best-effort
    // by nature — webfont glyphs and cross-origin images may not survive the
    // round-trip. Always answers (success or error), so the host's pending
    // state can't get stuck.
    async function postSelectCapture() {
        if (!hostOrigin || !selectCurrent) return
        try {
            const dataUrl = await domToPng(selectCurrent)
            post(companionMessage('select-capture', { dataUrl }), hostOrigin)
        } catch (error) {
            post(companionMessage('select-capture', { error: String(error?.message || error) }), hostOrigin)
        }
    }

    function onSelectPointerMove(event) {
        // Hover only drives the outline while nothing is locked.
        if (selectLocked) return
        highlightAt(event.clientX, event.clientY)
    }

    function onSelectPointerDown(event) {
        // A click/tap locks the element under the point (touch has no hover —
        // the tap IS the pointing gesture). preventDefault also keeps it from
        // focusing/activating anything underneath.
        event.preventDefault()
        const el = document.elementsFromPoint(event.clientX, event.clientY).find((node) => !isOwnSelectNode(node))
        if (!el) return
        selectLastPoint = null
        setSelectCurrent(el, true)
    }

    function blockSelectEvent(event) {
        event.preventDefault()
    }

    function onSelectScroll() {
        // The page still scrolls under the overlay (wheel/touch chaining). A
        // locked selection sticks to its element (redraw its rect); a hover
        // one re-aims at the last pointer position so the outline tracks the
        // element now under it.
        if (!selectLocked && selectLastPoint) highlightAt(selectLastPoint.x, selectLastPoint.y)
        else drawSelectOutline()
    }

    function setSelectMode(enabled) {
        if (enabled === !!selectOverlay) return
        if (!enabled) {
            window.removeEventListener('scroll', onSelectScroll, true)
            selectOverlay.remove()
            selectOutline.remove()
            selectOverlay = null
            selectOutline = null
            selectLastPoint = null
            selectCurrent = null
            selectLocked = false
            return
        }
        selectOverlay = document.createElement('div')
        selectOverlay.style.cssText = 'position:fixed;inset:0;z-index:2147483646;cursor:crosshair;background:transparent;'
        selectOverlay.addEventListener('pointermove', onSelectPointerMove)
        selectOverlay.addEventListener('pointerdown', onSelectPointerDown)
        selectOverlay.addEventListener('click', blockSelectEvent)
        selectOverlay.addEventListener('contextmenu', blockSelectEvent)
        selectOutline = document.createElement('div')
        selectOutline.style.cssText =
            'position:fixed;z-index:2147483647;pointer-events:none;display:none;' +
            'outline:2px dashed #e5484d;outline-offset:-2px;'
        window.addEventListener('scroll', onSelectScroll, true)
        const root = document.body || document.documentElement
        root.appendChild(selectOverlay)
        root.appendChild(selectOutline)
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
