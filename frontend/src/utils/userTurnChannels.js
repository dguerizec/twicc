// Whether any "agent finished working" notification channel is enabled.
//
// The per-session ``mute_on_user_turn`` flag gates four channels at once: the
// in-app toast, the sound, the browser notification and the Apprise push. When
// all four are off, toggling that flag changes nothing observable, so the
// session header explains it instead of leaving the user guessing.
//
// Scope: user-turn channels only. A sound configured for "Agent needs your
// attention", or an Apprise target that opted into ``notifyPendingRequest``
// alone, is not a channel this flag can silence.

// ``NOTIFICATION_SOUNDS.NONE`` is deliberately not imported: its module
// registers a ``document`` listener at load time, which would make this helper
// unimportable outside a browser. The template of NotificationSettings.vue
// already compares against the same literal.
const SOUND_NONE = 'none'

/**
 * Whether at least one external target would receive the user-turn push.
 *
 * A target opts in through ``notifyUserTurn``; an absent key means opted in
 * (the backend reads it the same way, see ``external_notifications.py``).
 *
 * @param {Array<object>|null|undefined} targets
 * @returns {boolean}
 */
function hasUserTurnExternalTarget(targets) {
    if (!Array.isArray(targets)) return false
    return targets.some(target => target?.enabled && target.notifyUserTurn !== false)
}

/**
 * Whether any channel would deliver an "agent finished working" notification.
 *
 * The browser channel is judged on the user's own switch, never on
 * ``Notification.permission``: the permission is not reactive, and ignoring it
 * errs on the safe side — we stay silent rather than wrongly claim that
 * nothing is enabled.
 *
 * @param {object} settings - The settings store (or a plain object with the same keys)
 * @returns {boolean}
 */
export function hasAnyUserTurnChannel(settings) {
    if (!settings) return false
    return Boolean(settings.notifUserTurnToast)
        || Boolean(settings.notifUserTurnSound && settings.notifUserTurnSound !== SOUND_NONE)
        || Boolean(settings.notifUserTurnBrowser)
        || hasUserTurnExternalTarget(settings.externalNotificationTargets)
}
