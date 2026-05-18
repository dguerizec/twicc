/**
 * Helpers for deciding whether to move focus programmatically — e.g. from a
 * global keyboard shortcut (Alt+Shift+M) or an auto-focus action (Approve
 * button when a pending request appears).
 *
 * `canStealFocus()` is the all-in-one helper most callers want. The two
 * lower-level helpers (`hasBlockingOverlay`, `isFocusInEditable`) are exposed
 * for the rare case where a caller needs to mix them on its own — e.g.
 * Alt+Shift+M closes the Agent Settings popover even if the focus is inside
 * one of its inputs, so it skips the in-editable check on that branch.
 */

/**
 * Returns true if focus can be stolen without disrupting the user:
 * no blocking overlay is open, and focus isn't already in an editable element.
 *
 * `allowPopoverContaining` is forwarded to `hasBlockingOverlay`; see its
 * doc for the carve-out rule.
 */
export function canStealFocus({ allowPopoverContaining = null } = {}) {
    if (hasBlockingOverlay({ allowPopoverContaining })) return false
    if (isFocusInEditable()) return false
    return true
}

/**
 * Returns true if any overlay that should block global focus management is
 * open: wa-popover, wa-dialog, wa-dropdown, or a text-selection comment.
 *
 * `allowPopoverContaining` lets the caller exclude a specific popover by
 * matching the given selector against the popover's subtree. The selector
 * lives at the call site, so this helper stays agnostic about which popovers
 * exist in the app.
 */
export function hasBlockingOverlay({ allowPopoverContaining = null } = {}) {
    const popovers = document.querySelectorAll('wa-popover[open]')
    for (const popover of popovers) {
        if (allowPopoverContaining && popover.querySelector(allowPopoverContaining)) {
            continue
        }
        return true
    }
    return document.querySelector('wa-dialog[open], wa-dropdown[open], .text-selection-comment') !== null
}

/**
 * Returns true if document.activeElement is an editable element.
 * The xterm helper textarea is treated as non-editable on purpose — terminals
 * aren't "edit fields" from the user's perspective.
 */
export function isFocusInEditable() {
    const active = document.activeElement
    if (!active) return false
    if (active.closest('.xterm') !== null) return false
    return (
        active.tagName === 'INPUT' ||
        active.tagName === 'TEXTAREA' ||
        active.tagName === 'WA-INPUT' ||
        active.tagName === 'WA-TEXTAREA' ||
        active.tagName === 'WA-SELECT' ||
        active.isContentEditable
    )
}
