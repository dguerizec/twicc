import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')
const popoverSource = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
const websocketSource = readFileSync(new URL('../composables/useWebSocket.js', import.meta.url), 'utf8')
const browserSource = readFileSync(new URL('../components/browser/BrowserPane.vue', import.meta.url), 'utf8')
const backendSource = readFileSync(new URL('../../../src/twicc/asgi.py', import.meta.url), 'utf8')

const FIELDS = [
    ['Public', 'publicBaseUrl'],
    ['Share', 'shareBaseUrl'],
    ['Peer', 'peerBaseUrl'],
]

test('External, Share, and Peer origins expose a fail-closed getter', () => {
    for (const [name, key] of FIELDS) {
        assert.match(settingsSource, new RegExp(
            'getUsable' + name + 'BaseUrl:[^\\n]+usablePublicOrigin\\(state\\.' + key + '\\)',
        ))
    }
})

test('the store has one non-optimistic per-field send action', () => {
    for (const [name] of FIELDS) {
        assert.doesNotMatch(settingsSource, new RegExp('set' + name + 'BaseUrl\\('))
    }
    assert.match(settingsSource, /async sendOriginSetting\(field, value, requestId\)/)
    assert.match(settingsSource, /sendSyncedSettings\(\{ \[field\]: value \}, _settingsVersion, requestId\)/)
    assert.doesNotMatch(settingsSource, /this\.\w+BaseUrl = value/)
})

test('each Apply sends its trimmed field and snapshots its visible text', () => {
    assert.match(popoverSource, /applyOriginSetting\('publicBaseUrl', publicBaseUrlInput\)/)
    assert.match(popoverSource, /applyOriginSetting\('shareBaseUrl', shareBaseUrlInput\)/)
    assert.match(popoverSource, /applyOriginSetting\('peerBaseUrl', peerBaseUrlInput\)/)
    assert.match(popoverSource, /const value = result\.patch\[field\]/)
    assert.match(popoverSource, /pendingOriginWrites\.set\(requestId, \{ field, input: inputRef\.value \}\)/)
    assert.match(popoverSource, /store\.sendOriginSetting\(field, value, requestId\)/)
})

test('Apply renders field errors before it returns on an empty patch', () => {
    assert.match(
        popoverSource,
        /setOriginError\(field, result\.errors\)\s+if \(result\.errors\.length \|\| !Object\.keys\(result\.patch\)\.length\) return/,
    )
})

test('the Settings result protocol carries one correlation ID end to end', () => {
    assert.match(popoverSource, /import \{ generateUUID \} from '\.\.\/\.\.\/utils\/crypto'/)
    assert.match(popoverSource, /const requestId = generateUUID\(\)/)
    assert.doesNotMatch(popoverSource, /crypto\.randomUUID/)
    assert.match(websocketSource, /request_id: requestId/)
    assert.match(backendSource, /request_id = content\.get\("request_id"\)/)
    assert.match(backendSource, /"type": "synced_settings_result"/)
    assert.match(websocketSource, /twicc:synced-settings-result/)
    assert.match(popoverSource, /pendingOriginWrites\.get\(payload\?\.request_id\)/)
})

test('correlated results adopt accepted values and show rejected field errors', () => {
    assert.match(popoverSource, /resolveOriginSettingResult\(/)
    assert.match(popoverSource, /if \(result\.status === 'accepted'\)/)
    assert.match(popoverSource, /originInputRefs\[result\.field\]\.value = result\.value/)
    assert.match(popoverSource, /setOriginError\(result\.field, result\.errors\)/)
    assert.match(websocketSource, /applySyncedSettings\(msg\.settings, msg\.version\)/)
})

test('the popover subscribes and unsubscribes the correlated result handler', () => {
    assert.match(
        popoverSource,
        /window\.addEventListener\('twicc:synced-settings-result', onOriginSettingsResult\)/,
    )
    assert.match(
        popoverSource,
        /window\.removeEventListener\('twicc:synced-settings-result', onOriginSettingsResult\)/,
    )
})

test('broadcast resyncs preserve typed text without settling correlated writes', () => {
    assert.match(popoverSource, /watch\(\(\) => store\.getPublicBaseUrl/)
    assert.match(popoverSource, /watch\(\(\) => store\.getShareBaseUrl/)
    assert.match(popoverSource, /watch\(\(\) => store\.getPeerBaseUrl/)
    assert.match(popoverSource, /refreshOriginInput\(inputRef\.value, oldValue, value\)/)
    assert.doesNotMatch(popoverSource, /function refreshOriginField[^}]+pendingOriginWrites/s)
})

test('typing invalidates older results for only that field', () => {
    for (const field of ['publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl']) {
        assert.match(popoverSource, new RegExp(
            "discardOriginSettingWrites\\(pendingOriginWrites, '" + field + "'\\)",
        ))
    }
})

test('disconnect discards correlation IDs whose results cannot arrive', () => {
    assert.match(
        popoverSource,
        /watch\(\(\) => dataStore\.wsConnected,[\s\S]*?if \(!connected\) pendingOriginWrites\.clear\(\)/,
    )
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
