// frontend/src/utils/version.test.js
//
// Run with:  node --test src/utils/version.test.js   (from the frontend dir)
//
// No test framework is wired into the frontend, so this uses Node's built-in
// test runner (node:test) — zero dependencies, no node_modules required.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { compareVersions } from './version.js'

test('orders by numeric magnitude, not lexicographically', () => {
    // The exact regression this fixes: a string comparison returns
    // "1.9.2" >= "1.10.0" === true, silencing the "update available" toast.
    // Numeric comparison must report 1.9.2 as OLDER than 1.10.0.
    assert.equal(compareVersions('1.9.2', '1.10.0'), -1)
    assert.equal(compareVersions('1.10.0', '1.9.2'), 1)

    // Sanity: the naive string comparison really is wrong (documents the trap).
    assert.equal('1.9.2' >= '1.10.0', true)
})

test('handles equality and the 1.9.2 -> 1.9.3 notification path', () => {
    assert.equal(compareVersions('1.9.2', '1.9.2'), 0)
    // A 1.9.2 client must still be notified about 1.9.3 (older -> -1).
    assert.equal(compareVersions('1.9.2', '1.9.3'), -1)
})

test('treats missing trailing segments as zero', () => {
    assert.equal(compareVersions('1.9', '1.9.0'), 0)
    assert.equal(compareVersions('1.9.0', '1.9'), 0)
})

test('compares each segment numerically (double digits)', () => {
    assert.equal(compareVersions('1.9.11', '1.9.9'), 1)
    assert.equal(compareVersions('2.0.0', '1.99.99'), 1)
    assert.equal(compareVersions('1.10.0', '1.9.99'), 1)
})
