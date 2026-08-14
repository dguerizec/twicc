// frontend/src/utils/resync.test.js
//
// Run with:  node --test src/utils/resync.test.js   (from the frontend dir)
//
// Only the policy is tested here: it is pure, so it needs no DOM. The wiring
// around it (sessionStorage, visibilitychange, the toast) is thin glue over
// this decision.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { decideResyncAction, RELOAD_COOLDOWN_MS } from './resync.js'

const NOW = 1_700_000_000_000

test('a visible tab reloads immediately', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: null, now: NOW }),
        'reload',
    )
})

test('a hidden tab waits instead of reloading for nobody', () => {
    // It is also the tab most likely to have fallen behind, so reloading it
    // where no one is looking would burn the exact resource it is short of.
    assert.equal(
        decideResyncAction({ visible: false, lastReload: null, now: NOW }),
        'defer',
    )
})

test('a second loss inside the cooldown asks instead of reloading', () => {
    // The guard that matters: the loss happens because the client cannot keep
    // up, and a reload gives it more work. Without this, a chronically slow
    // client reloads forever.
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - 1000, now: NOW }),
        'ask',
    )
})

test('the cooldown outranks visibility', () => {
    // Otherwise a hidden tab would queue a deferred reload that fires the
    // moment the user returns, right after the one that just happened.
    assert.equal(
        decideResyncAction({ visible: false, lastReload: NOW - 1000, now: NOW }),
        'ask',
    )
})

test('reloading is allowed again once the cooldown has elapsed', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS, now: NOW }),
        'reload',
    )
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS - 1, now: NOW }),
        'reload',
    )
})

test('the boundary is exclusive — one millisecond short still asks', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS + 1, now: NOW }),
        'ask',
    )
})

test('a corrupted or absent stamp is treated as "never reloaded"', () => {
    // readLastReload() returns null for anything unparseable, so a wiped or
    // hand-edited sessionStorage must not wedge the tab into 'ask' forever.
    assert.equal(
        decideResyncAction({ visible: true, lastReload: null, now: NOW }),
        'reload',
    )
})
