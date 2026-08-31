// frontend/src/utils/chatBlocks.test.js
//
// Run with:  node --test src/utils/chatBlocks.test.js   (from the frontend dir)

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    BLOCK_REACHED_TOLERANCE_PX,
    blockTargets,
    nextBlockTarget,
    prevBlockTarget,
} from './chatBlocks.js'

// Shorthand builders: `s` = block start, `i` = inner item, `d` = day separator.
const s = () => ({ isBlockStart: true })
const i = () => ({})
const d = () => ({ isDaySeparator: true })

// A transcript whose blocks start at these scroll offsets.
const TARGETS = [0, 1, 4, 5]
const TOPS = { 0: 0, 1: 100, 4: 900, 5: 1400 }
const topOf = (index) => TOPS[index]

test('blockTargets picks every block start', () => {
    // user | assistant×3 | user | assistant×2
    const items = [s(), s(), i(), i(), s(), s(), i()]
    assert.deepEqual(blockTargets(items), [0, 1, 4, 5])
})

test('blockTargets prefers the day separator introducing a block', () => {
    const items = [s(), i(), d(), s(), i()]
    assert.deepEqual(blockTargets(items), [0, 2])
})

test('blockTargets tolerates empty and sparse input', () => {
    assert.deepEqual(blockTargets([]), [])
    assert.deepEqual(blockTargets(null), [])
    assert.deepEqual(blockTargets([undefined, s()]), [1])
})

test('nextBlockTarget returns the first block below the viewport top', () => {
    assert.equal(nextBlockTarget(TARGETS, topOf, 0), 1)
    assert.equal(nextBlockTarget(TARGETS, topOf, 100), 4)
    assert.equal(nextBlockTarget(TARGETS, topOf, 500), 4)
    assert.equal(nextBlockTarget(TARGETS, topOf, 900), 5)
})

test('nextBlockTarget skips a block the scroller undershot', () => {
    // Landed a few pixels short of the block at 900: it counts as reached, so
    // the next press moves on instead of re-targeting it (the dead click).
    assert.equal(nextBlockTarget(TARGETS, topOf, 900 - 3), 5)
    assert.equal(nextBlockTarget(TARGETS, topOf, 900 - BLOCK_REACHED_TOLERANCE_PX + 1), 5)
})

test('nextBlockTarget keeps a block that is genuinely below the fold', () => {
    assert.equal(nextBlockTarget(TARGETS, topOf, 900 - BLOCK_REACHED_TOLERANCE_PX - 1), 4)
})

test('nextBlockTarget returns null inside the last block', () => {
    assert.equal(nextBlockTarget(TARGETS, topOf, 1400), null)
    assert.equal(nextBlockTarget(TARGETS, topOf, 3000), null)
    assert.equal(nextBlockTarget([], topOf, 0), null)
})

test('prevBlockTarget re-aligns the current block before leaving it', () => {
    // Deep inside the block that starts at 100.
    assert.equal(prevBlockTarget(TARGETS, topOf, 500), 1)
    // Scrolled a little past a block start: still that block.
    assert.equal(prevBlockTarget(TARGETS, topOf, 900 + BLOCK_REACHED_TOLERANCE_PX + 1), 4)
})

test('prevBlockTarget leaves for the previous block once aligned', () => {
    assert.equal(prevBlockTarget(TARGETS, topOf, 900), 1)
    assert.equal(prevBlockTarget(TARGETS, topOf, 100), 0)
    // An overshoot within the tolerance still counts as aligned.
    assert.equal(prevBlockTarget(TARGETS, topOf, 900 + 3), 1)
})

test('prevBlockTarget returns null at the very first block', () => {
    assert.equal(prevBlockTarget(TARGETS, topOf, 0), null)
    assert.equal(prevBlockTarget(TARGETS, topOf, 10), null)
    assert.equal(prevBlockTarget([], topOf, 500), null)
})
