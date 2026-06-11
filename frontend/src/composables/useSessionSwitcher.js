// frontend/src/composables/useSessionSwitcher.js
//
// State machine for the Ctrl+` session switcher — a browser-friendly take on
// the OS "Alt-Tab" gesture, cycling through the most-recently-visited sessions.
//
// Why Ctrl+` (and not Meta/Cmd+Tab): every modifier+Tab combo is grabbed before
// the page sees it (Alt+Tab / Cmd+Tab are the OS app/window switchers, Ctrl+Tab
// is the browser's own tab cycler and isn't reliably preventable). Ctrl is the
// only modifier left whose keyup we can observe to "commit on release" without
// the Windows lone-Alt menu trap, and it's used uniformly on every OS — never
// swapped to Cmd on macOS, where Cmd+` is itself an OS window-cycle shortcut.
//
// Gesture, driven by the global key handlers in App.vue:
//   - Tap Ctrl+` and release   → jump straight to the previous session.
//   - Hold Ctrl, tap ` (or ↓)  → panel opens, cursor steps down the MRU list.
//   - Ctrl+Shift+` (or ↑)      → step back up. Wraps around at both ends.
//   - Release Ctrl             → commit the highlighted session.
//   - Escape                   → cancel, no navigation.
// Mouse hover/click inside the panel move/commit the cursor directly.
//
// The session list is snapshotted at engage time so it stays stable while
// cycling (navigation only happens on commit, so the MRU can't reshuffle
// mid-gesture, but snapshotting also guards against any incidental store churn).

import { ref } from 'vue'
import { useDataStore } from '../stores/data'

// Delay before the panel actually appears, so a quick tap-and-release (jump to
// previous) never flashes the overlay.
const ANTI_FLASH_MS = 150
// While the cycle key auto-repeats (held down), advance at most this often, so
// holding ` scrolls at a readable pace instead of racing through the list.
const REPEAT_THROTTLE_MS = 250

// Singleton reactive state shared between App.vue's key handlers and the
// SessionSwitcher overlay component.
const active = ref(false)   // gesture in progress (Ctrl held since first press)
const visible = ref(false)  // overlay actually rendered (after anti-flash)
const items = ref([])       // snapshot: [{ session, path }], newest first
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
    items.value = []
    cursor.value = 0
    lastStepAt = 0
}

// Begin a gesture: snapshot the MRU list and pick the starting cursor.
//
// When the gesture starts from a session, that session is the MRU head (index
// 0), so the natural target is the *previous* one (index 1) — classic Alt-Tab.
// When it starts from a non-session page (project list, home…), there's no
// "current" session at the head, so the most recent session (index 0) is the
// target. No-op when there's nowhere to go.
function engage(currentSessionId) {
    const store = useDataStore()
    const list = store.mruSwitcherSessions
    if (list.length === 0) return false
    const firstIsCurrent = !!currentSessionId && list[0].session.id === currentSessionId
    if (firstIsCurrent && list.length < 2) return false  // only the current session
    items.value = list
    active.value = true
    visible.value = false
    cursor.value = firstIsCurrent ? 1 : 0
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

// Reveal the panel immediately (any interaction past the initial press means
// the user is browsing, not tapping — no reason to keep waiting on the timer).
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

// The cycle key (`Ctrl+\``): engages on the first press, then steps on each
// subsequent press. Shift reverses direction; autorepeat is throttled.
// `currentSessionId` (the session in view, if any) is only used on engage.
function onCycleKey({ backward = false, repeat = false, currentSessionId = null } = {}) {
    if (!active.value) {
        engage(currentSessionId)
        return
    }
    if (throttledRepeat(repeat)) return
    stepBy(backward ? -1 : 1)
    lastStepAt = performance.now()
    revealNow()
}

// Arrow keys move the cursor once the panel is engaged (↓ next, ↑ previous).
function onArrow({ backward = false, repeat = false } = {}) {
    if (!active.value) return
    if (throttledRepeat(repeat)) return
    stepBy(backward ? -1 : 1)
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
    // Lazy import to avoid a static composable→router cycle (which would break
    // Vite HMR — see the circular-import notes in CLAUDE.md).
    const { router } = await import('../router')
    if (router.currentRoute.value.fullPath !== item.path) {
        router.push(item.path)
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
        items,
        cursor,
        onCycleKey,
        onArrow,
        pointTo,
        commit,
        commitTo,
        cancel,
    }
}
