export const COMPUTE_PENDING_RECOVERY_HINT_DELAY_MS = 2 * 60 * 1000
export const COMPUTE_PENDING_RESTART_HINT_DELAY_MS = 10 * 60 * 1000


/**
 * Own the delayed recovery and restart guidance for one session-detail view.
 *
 * ``update(true)`` restarts the delay. ``update(false)`` and ``dispose()``
 * cancel it, so a completed or replaced session cannot reveal stale guidance.
 */
export function createComputePendingHint({
    setPhase,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
}) {
    let recoveryTimerId = null
    let restartTimerId = null

    function cancelTimers() {
        if (recoveryTimerId !== null) clearTimer(recoveryTimerId)
        if (restartTimerId !== null) clearTimer(restartTimerId)
        recoveryTimerId = null
        restartTimerId = null
    }

    function update(pending) {
        cancelTimers()
        setPhase(null)
        if (!pending) return

        recoveryTimerId = setTimer(() => {
            recoveryTimerId = null
            setPhase('recovery')
        }, COMPUTE_PENDING_RECOVERY_HINT_DELAY_MS)
        restartTimerId = setTimer(() => {
            restartTimerId = null
            setPhase('restart')
        }, COMPUTE_PENDING_RESTART_HINT_DELAY_MS)
    }

    function dispose() {
        cancelTimers()
        setPhase(null)
    }

    return { update, dispose }
}
