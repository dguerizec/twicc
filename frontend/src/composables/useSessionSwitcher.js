// frontend/src/composables/useSessionSwitcher.js
//
// State machine for the session switcher — a browser-friendly take on the OS
// "Alt-Tab", with two sources:
//   - Ctrl+`            → recently-visited sessions (MRU).
//   - Ctrl+Shift+`      → the sessions currently shown in the sidebar list.
//   - Shift (panel open) → toggles between the two sources live.
//
// Why Ctrl+` (and not Meta/Cmd+Tab): every modifier+Tab combo is grabbed before
// the page sees it (Alt+Tab / Cmd+Tab are OS switchers, Ctrl+Tab is the browser
// tab cycler). Ctrl is the only modifier whose keyup we can observe to "commit
// on release" without the Windows lone-Alt menu trap, used uniformly on every OS
// — never swapped to Cmd on macOS, where Cmd+` is itself an OS shortcut.
//
// Gesture, driven by the global key handlers in App.vue:
//   - Tap and release          → jump straight to the target.
//   - Hold Ctrl, tap ` (or ↓)  → panel opens, cursor steps down.
//   - ↑ (or ↓)                 → move the cursor both ways.
//   - Tap Shift (Ctrl held)    → flip source MRU ↔ displayed.
//   - Release Ctrl             → commit. Escape → cancel.
// Mouse hover/click inside the panel move/commit the cursor directly.
//
// The list is snapshotted at engage (and at each source toggle) so it stays
// stable while cycling.

import { ref } from 'vue'
import { useDataStore } from '../stores/data'
import { sessionRouteLocation } from '../utils/sessionRoute'

// Delay before the panel appears, so a quick tap-and-release never flashes it.
const ANTI_FLASH_MS = 150
// While the cycle key (or arrows) auto-repeats (held down), advance at most this
// often — fast enough to scan a long list, slow enough to stay controllable.
const REPEAT_THROTTLE_MS = 90

// Singleton reactive state shared between App.vue's key handlers and the
// SessionSwitcher overlay component.
const active = ref(false)   // gesture in progress (Ctrl held since first press)
const visible = ref(false)  // overlay actually rendered (after anti-flash)
const mode = ref('mru')     // 'mru' | 'list' — active source
const items = ref([])       // snapshot: [{ session, path? }], in order
const cursor = ref(0)       // highlighted index into items

let antiFlashTimer = null
let lastStepAt = 0

function reset() {
    if (antiFlashTimer) {
        clearTimeout(antiFlashTimer)
        antiFlashTimer = null
    }
    active.value = false
    visible.value = false
    mode.value = 'mru'
    items.value = []
    cursor.value = 0
    lastStepAt = 0
}

// Snapshot of the list backing a given source.
function snapshotFor(targetMode) {
    const store = useDataStore()
    return targetMode === 'list' ? store.displayedSwitcherSessions : store.mruSwitcherSessions
}

// Where the cursor lands when a list is first shown.
function startCursor(targetMode, list, currentSessionId) {
    if (targetMode === 'mru') {
        // MRU head is the current session, so the natural target is the previous
        // one (index 1); from a non-session page the most recent (index 0).
        const firstIsCurrent = !!currentSessionId && list[0]?.session.id === currentSessionId
        return firstIsCurrent ? 1 : 0
    }
    // Displayed list: land on the current session's row if present, else the top.
    const idx = currentSessionId ? list.findIndex(it => it.session.id === currentSessionId) : -1
    return idx >= 0 ? idx : 0
}

// Whether a source has somewhere worth going (MRU needs a target beyond the
// current session; the displayed list just needs at least one row).
function hasTarget(targetMode, list, currentSessionId) {
    if (list.length === 0) return false
    if (targetMode === 'mru') {
        const firstIsCurrent = !!currentSessionId && list[0].session.id === currentSessionId
        if (firstIsCurrent && list.length < 2) return false
    }
    return true
}

function engage(targetMode, currentSessionId) {
    const list = snapshotFor(targetMode)
    if (!hasTarget(targetMode, list, currentSessionId)) return false
    items.value = list
    mode.value = targetMode
    active.value = true
    visible.value = false
    cursor.value = startCursor(targetMode, list, currentSessionId)
    lastStepAt = performance.now()
    antiFlashTimer = setTimeout(() => {
        visible.value = true
        antiFlashTimer = null
    }, ANTI_FLASH_MS)
    return true
}

function stepBy(delta) {
    const n = items.value.length
    if (n === 0) return
    cursor.value = ((cursor.value + delta) % n + n) % n
}

// Reveal the panel immediately (any interaction past the initial press means the
// user is browsing, not tapping).
function revealNow() {
    if (antiFlashTimer) {
        clearTimeout(antiFlashTimer)
        antiFlashTimer = null
    }
    visible.value = true
}

function throttledRepeat(repeat) {
    if (!repeat) return false
    return performance.now() - lastStepAt < REPEAT_THROTTLE_MS
}

// The cycle key (Ctrl+`). `engageMode` selects the source on the first press
// ('list' when Shift is held, else 'mru'); afterwards each press steps forward.
function onCycleKey({ engageMode = 'mru', currentSessionId = null, repeat = false } = {}) {
    if (!active.value) {
        engage(engageMode, currentSessionId)
        return
    }
    if (throttledRepeat(repeat)) return
    stepBy(1)
    lastStepAt = performance.now()
    revealNow()
}

// Arrow keys move the cursor both ways once engaged (↓ next, ↑ previous).
function onArrow({ backward = false, repeat = false } = {}) {
    if (!active.value) return
    if (throttledRepeat(repeat)) return
    stepBy(backward ? -1 : 1)
    lastStepAt = performance.now()
    revealNow()
}

// Tapping Shift while the panel is open flips the source, keeping the same
// session highlighted when it exists in the other list. No-op if the other
// source is empty (nothing to switch to).
function toggleMode(currentSessionId = null) {
    if (!active.value) return
    const targetMode = mode.value === 'mru' ? 'list' : 'mru'
    const list = snapshotFor(targetMode)
    if (list.length === 0) return
    const keepId = items.value[cursor.value]?.session.id
    items.value = list
    mode.value = targetMode
    const idx = keepId ? list.findIndex(it => it.session.id === keepId) : -1
    cursor.value = idx >= 0 ? idx : startCursor(targetMode, list, currentSessionId)
    lastStepAt = performance.now()
    revealNow()
}

// Mouse hover: move the highlight without committing.
function pointTo(index) {
    if (!active.value) return
    if (index < 0 || index >= items.value.length) return
    cursor.value = index
}

async function commit() {
    if (!active.value) return
    const item = items.value[cursor.value]
    reset()
    if (!item) return
    // Lazy import to avoid a static composable→router cycle (see CLAUDE.md).
    const { router } = await import('../router')
    const current = router.currentRoute.value
    // Already on this session? Stay put — don't even switch sub-tab. (Matters for
    // the displayed list, whose target is the session's default route and would
    // otherwise yank you off the Files/Git/… tab you're currently on.)
    if (item.session && item.session.id === current.params.sessionId) return
    // MRU items carry the exact last-visited path; displayed items resolve to the
    // session's default route from the current context.
    const target = item.path ?? sessionRouteLocation(item.session, current)
    const targetFullPath = typeof target === 'string' ? target : router.resolve(target).fullPath
    if (current.fullPath !== targetFullPath) {
        router.push(target)
    }
}

// Mouse click: commit the clicked row regardless of Ctrl state.
async function commitTo(index) {
    if (!active.value) return
    if (index >= 0 && index < items.value.length) cursor.value = index
    await commit()
}

function cancel() {
    reset()
}

export function useSessionSwitcher() {
    return {
        active,
        visible,
        mode,
        items,
        cursor,
        onCycleKey,
        onArrow,
        toggleMode,
        pointTo,
        commit,
        commitTo,
        cancel,
    }
}
