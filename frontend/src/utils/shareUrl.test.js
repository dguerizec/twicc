// The §7.4 parity fixture, JS side — driven by the SAME file as
// tests/test_share_url_parity.py. Never edit one side's expectations.
// Imports the dependency-free core module: shareUrl.js pulls the Pinia
// store and is not importable under node --test.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const fixture = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/share_url_parity.json', import.meta.url), 'utf8'),
)

for (const c of fixture.cases) {
    test(`parity: ${c.name}`, () => {
        assert.equal(buildShareUrl(c.stored, fixture.url_path), c.expected)
    })
}

test('empty base stays empty after normalization', () => {
    assert.equal(normalizeShareBase(''), '')
    assert.equal(normalizeShareBase('   '), '')
})
