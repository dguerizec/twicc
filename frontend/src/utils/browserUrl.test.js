// frontend/src/utils/browserUrl.test.js
//
// Run with:  node --test src/utils/browserUrl.test.js   (from the frontend dir)
//
// No test framework is wired into the frontend, so this uses Node's built-in
// test runner (node:test) — zero dependencies, no node_modules required.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeBrowserUrl } from './browserUrl.js'

test('keeps explicit http(s) URLs (normalized by URL())', () => {
    assert.equal(normalizeBrowserUrl('http://localhost:3000'), 'http://localhost:3000/')
    assert.equal(normalizeBrowserUrl('https://example.com/app?x=1#y'), 'https://example.com/app?x=1#y')
    assert.equal(normalizeBrowserUrl('HTTPS://Example.COM'), 'https://example.com/')
})

test('adds http:// to localhost-ish schemeless input', () => {
    assert.equal(normalizeBrowserUrl('localhost:5173'), 'http://localhost:5173/')
    assert.equal(normalizeBrowserUrl('localhost'), 'http://localhost/')
    assert.equal(normalizeBrowserUrl('127.0.0.1:8000/admin/'), 'http://127.0.0.1:8000/admin/')
    assert.equal(normalizeBrowserUrl('192.168.1.42:3000'), 'http://192.168.1.42:3000/')
    assert.equal(normalizeBrowserUrl('0.0.0.0:5174'), 'http://0.0.0.0:5174/')
    assert.equal(normalizeBrowserUrl('myapp.local:3000'), 'http://myapp.local:3000/')
    assert.equal(normalizeBrowserUrl('site.test'), 'http://site.test/')
})

test('adds https:// to other schemeless input', () => {
    assert.equal(normalizeBrowserUrl('example.com'), 'https://example.com/')
    assert.equal(normalizeBrowserUrl('example.com:8443/x'), 'https://example.com:8443/x')
})

test('treats dotless host:port as a schemeless local host, not as a scheme', () => {
    assert.equal(normalizeBrowserUrl('devbox:9000'), 'http://devbox:9000/')
})

test('rejects non-http(s) schemes', () => {
    assert.equal(normalizeBrowserUrl('javascript:alert(1)'), null)
    assert.equal(normalizeBrowserUrl('file:///etc/passwd'), null)
    assert.equal(normalizeBrowserUrl('data:text/html,<b>x</b>'), null)
    assert.equal(normalizeBrowserUrl('ftp://example.com'), null)
    assert.equal(normalizeBrowserUrl('mailto:x@y.z'), null)
})

test('rejects empty / unparsable input', () => {
    assert.equal(normalizeBrowserUrl(''), null)
    assert.equal(normalizeBrowserUrl('   '), null)
    assert.equal(normalizeBrowserUrl(null), null)
    assert.equal(normalizeBrowserUrl(undefined), null)
    assert.equal(normalizeBrowserUrl('http://'), null)
})

test('trims surrounding whitespace', () => {
    assert.equal(normalizeBrowserUrl('  localhost:3000  '), 'http://localhost:3000/')
})
