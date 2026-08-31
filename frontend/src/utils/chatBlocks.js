// frontend/src/utils/chatBlocks.js

/**
 * Block navigation over a transcript's visual items.
 *
 * A "block" is one run of consecutive user messages, or the whole assistant
 * turn that follows it (messages, tool calls, collapsed groups). The store
 * already flags the first visual item of each run with `isBlockStart` — the
 * same flag the CSS uses to draw the card's top border, so what these helpers
 * jump to is exactly what the reader sees as one card.
 *
 * Targets are compared by SCROLL POSITION, never by anchor index. Landing on a
 * block is only ever approximate: the scroller places items from estimated
 * heights, then corrects as they measure, so the viewport routinely settles a
 * few pixels short of the block it just jumped to. By index, that reads as
 * "still in the previous block" and the next press re-targets the same block —
 * the click that appears to do nothing. In pixels, with a tolerance, a block
 * that starts within a hair of the viewport top counts as reached.
 *
 * Pure math: the caller owns the scroller and the items array.
 */

/**
 * How close a block's start must be to the viewport top to count as reached.
 * Wide enough to swallow the scroller's settling error, small enough that a
 * block genuinely below the fold is never skipped.
 */
export const BLOCK_REACHED_TOLERANCE_PX = 24

/**
 * Indices to scroll to, one per block, in document order.
 *
 * A day separator sits just *before* the block start it introduces (it is
 * inserted after the flags are computed, so it carries no `isBlockStart`).
 * Targeting the separator rather than the block start keeps the date visible
 * once the block is pinned to the top of the viewport.
 *
 * @param {Array} visualItems
 * @returns {number[]} Ascending indices into `visualItems`.
 */
export function blockTargets(visualItems) {
    const targets = []
    if (!visualItems) return targets
    for (let i = 0; i < visualItems.length; i++) {
        if (!visualItems[i]?.isBlockStart) continue
        targets.push(i > 0 && visualItems[i - 1]?.isDaySeparator ? i - 1 : i)
    }
    return targets
}

/**
 * The first block that starts below the current position.
 *
 * @param {number[]} targets - From `blockTargets`.
 * @param {(index: number) => number} topOf - Scroll offset of a target's first pixel.
 * @param {number} scrollTop - Current scroll position.
 * @param {number} [tolerance]
 * @returns {number|null} A target index, or null when no block follows.
 */
export function nextBlockTarget(targets, topOf, scrollTop, tolerance = BLOCK_REACHED_TOLERANCE_PX) {
    for (const target of targets) {
        if (topOf(target) > scrollTop + tolerance) return target
    }
    return null
}

/**
 * The last block that starts above the current position.
 *
 * Reading a long block and pressing "previous" first brings that block's own
 * start back to the top — its start is above the viewport, so it wins. Only
 * once aligned on it does the next press leave for the block before. This
 * mirrors how a media player's "previous track" restarts the current track,
 * and it falls out of the comparison rather than needing a separate flag.
 *
 * @param {number[]} targets - From `blockTargets`.
 * @param {(index: number) => number} topOf - Scroll offset of a target's first pixel.
 * @param {number} scrollTop - Current scroll position.
 * @param {number} [tolerance]
 * @returns {number|null} A target index, or null when nothing precedes.
 */
export function prevBlockTarget(targets, topOf, scrollTop, tolerance = BLOCK_REACHED_TOLERANCE_PX) {
    let found = null
    for (const target of targets) {
        if (topOf(target) >= scrollTop - tolerance) break
        found = target
    }
    return found
}
