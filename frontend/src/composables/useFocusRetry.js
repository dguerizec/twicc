import { nextTick } from 'vue'

// Persist a focus request across the frames following a tab activation. Two things make a single
// .focus() unreliable there: (1) the target may not exist yet — the panel is still navigating into view,
// and a field can render late, sometimes a LOT late (a slow-loading tree — e.g. Git status — only renders
// its search input once its data has arrived, seconds later); (2) once present, right after the panel
// activates the route-sync reveal focuses the routed tree item, stealing focus ~100-150ms later.
//
// So `attemptFn` reports three states each frame:
//   • truthy        → focus now HOLDS on the target (success)
//   • false         → the target exists but isn't holding focus yet (a steal/reveal in progress)
//   • null/undefined → the target isn't in the DOM yet (still loading)
//
// We run two budgets accordingly. While the target is absent we spend the generous `appearFrames` budget
// (we can't steal anything that doesn't exist, so waiting is cheap) until it shows up. Once it's present
// we switch to the tight `retryFrames` steal-protection budget, re-asserting until focus has held for
// `stableFrames` consecutive frames (the one-shot steal has passed) — then stop. A target that never
// appears, or focus the user deliberately moved and kept elsewhere, simply lapses when its budget runs out.
//
// Returns a `requestFocus(attemptFn)` function. Calling it again refreshes both budgets and swaps in the
// new target without spawning a second pump loop (latest request wins).
export function useFocusRetry({ appearFrames = 300, retryFrames = 60, stableFrames = 10 } = {}) {
    let appearLeft = 0 // frames still allowed for the target to first APPEAR (absent state)
    let framesLeft = 0 // steal-protection budget, spent only once the target is present
    let stable = 0
    let attempt = null

    function stop() {
        appearLeft = 0
        framesLeft = 0
        stable = 0
    }

    function pump() {
        if (appearLeft <= 0 && framesLeft <= 0) return
        const res = attempt ? attempt() : false
        if (res == null) {
            // Target not in the DOM yet — keep waiting on the appear budget, don't touch steal protection.
            appearLeft -= 1
            if (appearLeft > 0) requestAnimationFrame(pump)
            else stop()
            return
        }
        // Target present: from here it's pure steal protection.
        appearLeft = 0
        stable = res ? stable + 1 : 0
        if (stable >= stableFrames) {
            stop()
            return
        }
        framesLeft -= 1
        if (framesLeft > 0) requestAnimationFrame(pump)
        else stop()
    }

    return function requestFocus(attemptFn) {
        attempt = attemptFn
        const wasIdle = appearLeft <= 0 && framesLeft <= 0
        appearLeft = appearFrames
        framesLeft = retryFrames
        stable = 0
        if (wasIdle) nextTick(pump)
    }
}
