import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')
const popoverSource = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
const browserSource = readFileSync(new URL('../components/browser/BrowserPane.vue', import.meta.url), 'utf8')

const FIELDS = [
    ['Public', 'publicBaseUrl'],
    ['Share', 'shareBaseUrl'],
    ['Peer', 'peerBaseUrl'],
]

test('all three public-origin settings use the common normalizer', () => {
    for (const [name, key] of FIELDS) {
        assert.match(settingsSource, new RegExp(`set${name}BaseUrl\\(url\\)[\\s\\S]*?normalizePublicOrigin\\(url\\)`), key)
        assert.match(popoverSource, new RegExp(`normalizePublicOrigin\\(${key}Input\\.value\\)`), key)
    }
})

test('all three public-origin settings expose a fail-closed getter', () => {
    for (const [name, key] of FIELDS) {
        assert.match(settingsSource, new RegExp(`getUsable${name}BaseUrl:[^\\n]+usablePublicOrigin\\(state\\.${key}\\)`), key)
    }
})

test('the former External URL label is now External address', () => {
    assert.match(popoverSource, />External address <wa-icon/)
    assert.doesNotMatch(popoverSource, />External URL <wa-icon/)
})

test('the Browser companion falls back when the External address is invalid', () => {
    assert.match(
        browserSource,
        /usablePublicOrigin\(settingsStore\.getPublicBaseUrl\) \|\| window\.location\.origin/,
    )
})
