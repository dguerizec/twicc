// frontend/src/utils/browserUrlEntries.test.js
//
// Run with:  node --test src/utils/browserUrlEntries.test.js   (from the frontend dir)
//
// Saved Browser-pane URL entry ops — the JS mirror of the backend helpers in
// twicc/workspaces.py; semantics must stay aligned (see the matching backend
// tests in tests/test_browser_urls.py).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    addBrowserUrlEntry,
    defaultBrowserUrlEntry,
    removeBrowserUrlEntry,
    setDefaultBrowserUrlEntry,
} from './browserUrlEntries.js'

test('defaultBrowserUrlEntry picks the flagged entry, else the first', () => {
    assert.equal(defaultBrowserUrlEntry([]), null)
    assert.equal(defaultBrowserUrlEntry(null), null)
    assert.deepEqual(
        defaultBrowserUrlEntry([{ url: 'http://a.test' }, { url: 'http://b.test', default: true }]),
        { url: 'http://b.test', default: true }
    )
    assert.deepEqual(
        defaultBrowserUrlEntry([{ url: 'http://a.test' }, { url: 'http://b.test' }]),
        { url: 'http://a.test' }
    )
})

test('addBrowserUrlEntry makes the first URL the default', () => {
    assert.deepEqual(addBrowserUrlEntry([], 'http://a.test'), [
        { url: 'http://a.test', default: true },
    ])
})

test('addBrowserUrlEntry appends without moving the default', () => {
    const entries = [{ url: 'http://a.test', default: true }]
    assert.deepEqual(addBrowserUrlEntry(entries, 'http://b.test', { label: 'B' }), [
        { url: 'http://a.test', default: true },
        { url: 'http://b.test', label: 'B' },
    ])
})

test('addBrowserUrlEntry with setDefault moves the flag', () => {
    const entries = [{ url: 'http://a.test', default: true }]
    assert.deepEqual(addBrowserUrlEntry(entries, 'http://b.test', { setDefault: true }), [
        { url: 'http://a.test' },
        { url: 'http://b.test', default: true },
    ])
})

test('addBrowserUrlEntry is idempotent on a listed URL and updates its label', () => {
    const entries = [
        { url: 'http://a.test', default: true },
        { url: 'http://b.test', label: 'old' },
    ]
    assert.deepEqual(addBrowserUrlEntry(entries, 'http://b.test', { label: ' new ', setDefault: true }), [
        { url: 'http://a.test' },
        { url: 'http://b.test', label: 'new', default: true },
    ])
    // No label given → the existing label is preserved.
    assert.deepEqual(addBrowserUrlEntry(entries, 'http://b.test'), entries)
})

test('removeBrowserUrlEntry is idempotent and never promotes a new default', () => {
    const entries = [
        { url: 'http://a.test', default: true },
        { url: 'http://b.test' },
    ]
    assert.deepEqual(removeBrowserUrlEntry(entries, 'http://a.test'), [{ url: 'http://b.test' }])
    assert.deepEqual(removeBrowserUrlEntry(entries, 'http://absent.test'), entries)
})

test('setDefaultBrowserUrlEntry moves the flag; absent URL is a no-op', () => {
    const entries = [
        { url: 'http://a.test', default: true },
        { url: 'http://b.test' },
    ]
    assert.deepEqual(setDefaultBrowserUrlEntry(entries, 'http://b.test'), [
        { url: 'http://a.test' },
        { url: 'http://b.test', default: true },
    ])
    assert.deepEqual(setDefaultBrowserUrlEntry(entries, 'http://absent.test'), entries)
})

test('ops never mutate their input', () => {
    const entries = [{ url: 'http://a.test', default: true }]
    addBrowserUrlEntry(entries, 'http://b.test', { setDefault: true })
    removeBrowserUrlEntry(entries, 'http://a.test')
    setDefaultBrowserUrlEntry(entries, 'http://a.test')
    assert.deepEqual(entries, [{ url: 'http://a.test', default: true }])
})
