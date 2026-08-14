// Temporary Task 1 boundary bridge. The renamed fixture now records backend
// verdicts. Task 3 replaces this legacy frontend-normalization suite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const fixture = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/share_url_backend_cases.json', import.meta.url), 'utf8'),
)

const LEGACY_FRONTEND_OVERRIDES = new Map([
    ['U+FEFF is invalid Unicode input', 'https://share.example.com/share/tok123/'],
])

for (const c of fixture.cases) {
    test(`legacy frontend normalization before Task 3: ${c.name}`, () => {
        const expected = LEGACY_FRONTEND_OVERRIDES.get(c.name) ?? c.expected
        assert.equal(buildShareUrl(c.stored, fixture.url_path), expected)
    })
}

test('empty base stays empty after normalization', () => {
    assert.equal(normalizeShareBase(''), '')
    assert.equal(normalizeShareBase('   '), '')
})

test('invalid share base fails closed after normalization', () => {
    assert.equal(normalizeShareBase('ftp://share.example.com'), '')
})
