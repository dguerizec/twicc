/**
 * Focus helpers for the session chat surface (message input + pending request
 * form). Used by the global Alt+Shift+M shortcut, the per-session keyboard tab
 * navigation, the command palette "Focus Message Input" action — anything that
 * wants to land on "the primary thing the user can act on right now" in the
 * chat panel.
 */

/**
 * Returns the primary interactive target inside the active PendingRequestForm,
 * or null when no pending request is shown. Picks the element that "I want to
 * act on right now" maps to:
 *   - tool_approval initial    → Approve button (carries .auto-focused)
 *   - tool_approval deny mode  → deny reason textarea
 *   - tool_approval edit mode  → "Approve with changes" button (sole brand button left)
 *   - ask_user_question        → first option-card
 */
export function getPendingRequestPrimaryTarget() {
    const form = document.querySelector('.pending-request-form')
    if (!form) return null
    if (form.querySelector('.questions-container')) {
        return form.querySelector('.option-card')
    }
    return (
        form.querySelector('wa-button.auto-focused')
        || form.querySelector('.deny-reason-input')
        || form.querySelector('.pending-request-actions wa-button[variant="brand"]')
    )
}

/**
 * Focus the chat's primary interactive element. When a pending request is
 * active, that's the appropriate control inside the form (Approve button,
 * deny textarea, …); otherwise it's the message input textarea.
 *
 * Retries because the caller may invoke this right after a router.push() or
 * a wa-tab-show event — even when the target is already in the DOM
 * (keep-alive), the transition can re-blur it as Vue re-renders. We keep
 * trying until focus actually sticks, or the retry budget runs out (~1.5s).
 */
export function focusChatPrimary(retries = 30) {
    // A collapsed message input keeps its textarea hidden (display:none), which
    // can't take focus. Ask the composer to expand first — it re-shows and
    // focuses the textarea, and the retry below makes the focus stick. No-op when
    // a pending request is shown (the composer isn't mounted) or already expanded.
    document
        .querySelector('.message-input.collapsed')
        ?.dispatchEvent(new CustomEvent('twicc:expand-composer'))

    const target = getPendingRequestPrimaryTarget()
        || document.querySelector('.message-input wa-textarea')
    if (!target) {
        if (retries > 0) setTimeout(() => focusChatPrimary(retries - 1), 50)
        return
    }
    target.focus()
    if (document.activeElement !== target && retries > 0) {
        setTimeout(() => focusChatPrimary(retries - 1), 50)
    }
}
