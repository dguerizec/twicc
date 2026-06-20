import { nextTick } from 'vue'

// Persist a focus request across the frames following a tab activation. Two things make a single
// .focus() unreliable there: (1) the target may not be visible yet — the panel is still navigating
// into view, and a field can render a few frames late (e.g. once its tree has loaded); (2) right
// after the panel activates, the route-sync reveal focuses the routed tree item, stealing focus
// ~100-150ms later. So we don't trust one shot: each frame we call `attemptFn` (which focuses the
// target and returns whether it now HOLDS focus) and keep re-asserting until it has held for
// `stableFrames` consecutive frames — i.e. the one-shot steal has passed — or give up after
// `retryFrames`. A request that never becomes focusable, or focus the user deliberately moved and
// kept elsewhere, simply lapses.
//
// Returns a `requestFocus(attemptFn)` function. Calling it again refreshes the budget and swaps in
// the new target without spawning a second pump loop (latest request wins).
export function useFocusRetry({ retryFrames = 60, stableFrames = 10 } = {}) {
    let framesLeft = 0
    let stable = 0
    let attempt = null

    function pump() {
        if (framesLeft <= 0) return
        const held = !!attempt && attempt()
        stable = held ? stable + 1 : 0
        if (stable >= stableFrames) {
            framesLeft = 0
            return
        }
        framesLeft -= 1
        if (framesLeft > 0) requestAnimationFrame(pump)
    }

    return function requestFocus(attemptFn) {
        attempt = attemptFn
        const wasIdle = framesLeft <= 0
        framesLeft = retryFrames
        stable = 0
        if (wasIdle) nextTick(pump)
    }
}
