import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { normalizePublicOrigin, repairLegacyPublicOrigin, usablePublicOrigin } from './publicOrigin.js'

const cases = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/public_origin_cases.json', import.meta.url), 'utf8'),
)

test('normalizePublicOrigin matches the shared contract', () => {
    for (const c of cases.cases) {
        const result = normalizePublicOrigin(c.input)
        assert.deepEqual({ value: result.value, error: result.error }, { value: c.value, error: c.error }, c.name)
    }
})

test('repairLegacyPublicOrigin matches the shared contract', () => {
    for (const c of cases.repair_cases) {
        const result = repairLegacyPublicOrigin(c.input)
        assert.deepEqual({ value: result.value, error: result.error }, { value: c.value, error: c.error }, c.name)
    }
})

test('normalizePublicOrigin exposes normalized metadata', () => {
    const result = normalizePublicOrigin('HTTPS://Example.COM:8443/')
    assert.equal(result.scheme, 'https')
    assert.equal(result.hostname, 'example.com')
    assert.equal(result.port, 8443)
})

test('usablePublicOrigin fails closed for an invalid legacy value', () => {
    assert.equal(usablePublicOrigin('https://valid.example.com/'), 'https://valid.example.com')
    assert.equal(usablePublicOrigin('ftp://unsafe.example.com'), '')
})
