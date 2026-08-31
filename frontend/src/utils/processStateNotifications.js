export function getProcessStateNotificationEffects(msg, previousState, options) {
    const enteredUserTurn = msg.state === 'user_turn'
        && previousState?.state !== 'user_turn'
    const userTurnEnabled = enteredUserTurn && msg.mute_on_user_turn !== true
    const newPendingCount = msg.pending_requests?.length || 0
    const previousPendingCount = previousState?.pending_requests?.length || 0
    const pendingRequestGrew = newPendingCount > previousPendingCount

    return {
        markViewed: enteredUserTurn && options.isViewingSession,
        showUserTurnToast: userTurnEnabled && !options.isViewingSession
            && options.userTurnToastEnabled,
        playUserTurnSound: userTurnEnabled,
        sendUserTurnBrowser: userTurnEnabled && options.userTurnBrowserEnabled,
        showPendingRequestToast: pendingRequestGrew && !options.isViewingSession,
        playPendingRequestSound: pendingRequestGrew,
        sendPendingRequestBrowser: pendingRequestGrew
            && options.pendingRequestBrowserEnabled,
        newPendingCount,
    }
}
