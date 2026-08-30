import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTick, reactive } from 'vue'

import {
    CONNECTION_LOST_ERROR,
    NOT_CONNECTED_ERROR,
    ORIGIN_SETTINGS_RESULT_EVENT,
    PLAIN_HTTP_WARNING,
    useOriginSettingsForm,
} from './useOriginSettingsForm.js'

// A minimal stand-in for the browser's event target: the form only ever
// add/removes one listener and receives CustomEvent-shaped objects.
function fakeEventTarget() {
    const listeners = new Map()
    return {
        listeners,
        addEventListener(type, handler) { listeners.set(type, handler) },
        removeEventListener(type, handler) {
            if (listeners.get(type) === handler) listeners.delete(type)
        },
        emit(type, detail) { listeners.get(type)?.({ detail }) },
    }
}

function setup({
    stored = {},
    sendResult = true,
    locationHostname = 'app.example',
} = {}) {
    const sent = []
    const settingsStore = reactive({
        getPublicBaseUrl: stored.publicBaseUrl || '',
        getShareBaseUrl: stored.shareBaseUrl || '',
        getPeerBaseUrl: stored.peerBaseUrl || '',
        async sendOriginSetting(field, value, requestId) {
            sent.push({ field, value, requestId })
            return typeof sendResult === 'function' ? sendResult() : sendResult
        },
    })
    const dataStore = reactive({ wsConnected: true })
    const eventTarget = fakeEventTarget()
    const form = useOriginSettingsForm({
        settingsStore,
        dataStore,
        locationHostname,
        eventTarget,
    })
    form.startOriginSettingsForm()
    return { form, settingsStore, dataStore, eventTarget, sent }
}

// A backend result frame, as useWebSocket re-dispatches it.
function acceptedFrame(requestId, field, value) {
    return { status: 'accepted', request_id: requestId, settings: { [field]: value } }
}

function rejectedFrame(requestId, errors) {
    return { status: 'rejected', request_id: requestId, errors }
}

test('a section seed loads the stored value and flags an unusable one', () => {
    const { form } = setup({ stored: { publicBaseUrl: 'https://app.example', shareBaseUrl: 'NOT AN ORIGIN' } })

    form.seedOriginField('publicBaseUrl')
    assert.equal(form.publicBaseUrlInput.value, 'https://app.example')
    assert.equal(form.publicBaseUrlError.value, '')

    form.seedOriginField('shareBaseUrl')
    assert.equal(form.shareBaseUrlInput.value, 'NOT AN ORIGIN')
    assert.match(form.shareBaseUrlError.value, /hostname or an HTTP\(S\) origin/)
})

test('Apply sends the trimmed input, and the accepted result adopts the canonical value', async () => {
    const { form, sent } = setup()
    form.onOriginInputChange('publicBaseUrl', '  EXTERNAL.Example.COM  ')

    await form.applyOriginSetting('publicBaseUrl')

    // The frontend only performs the safe subset: it trims, and defers the
    // scheme, the case and every other verdict to Python.
    assert.equal(sent.length, 1)
    assert.deepEqual(
        { field: sent[0].field, value: sent[0].value },
        { field: 'publicBaseUrl', value: 'EXTERNAL.Example.COM' },
    )
    assert.equal(form.pendingOriginWriteCount(), 1)

    form.handleOriginSettingsResult(acceptedFrame(sent[0].requestId, 'publicBaseUrl', 'https://external.example.com'))
    assert.equal(form.publicBaseUrlInput.value, 'https://external.example.com')
    assert.equal(form.publicBaseUrlError.value, '')
    assert.equal(form.pendingOriginWriteCount(), 0)
})

test('the result arrives through the subscribed event, and stops after teardown', () => {
    const { form, eventTarget, sent } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    form.applyOriginSetting('publicBaseUrl')

    eventTarget.emit(ORIGIN_SETTINGS_RESULT_EVENT, acceptedFrame(sent[0].requestId, 'publicBaseUrl', 'https://x.example'))
    assert.equal(form.publicBaseUrlInput.value, 'https://x.example')

    form.stopOriginSettingsForm()
    assert.equal(eventTarget.listeners.size, 0)
})

test('a rejected result shows the backend message on its own field', async () => {
    const { form, sent } = setup()
    form.onOriginInputChange('shareBaseUrl', 'share.example.com')
    await form.applyOriginSetting('shareBaseUrl')

    form.handleOriginSettingsResult(rejectedFrame(sent[0].requestId, [
        { field: 'shareBaseUrl', code: 'origin_conflict_share_external_hostname' },
    ]))

    assert.match(form.shareBaseUrlError.value, /different hostname from the External address/)
    assert.equal(form.publicBaseUrlError.value, '')
})

test('typing during a write invalidates only that field, and its late result is ignored', async () => {
    const { form, sent } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    form.onOriginInputChange('shareBaseUrl', 'share.example.com')
    await form.applyOriginSetting('publicBaseUrl')
    await form.applyOriginSetting('shareBaseUrl')
    assert.equal(form.pendingOriginWriteCount(), 2)

    form.onOriginInputChange('publicBaseUrl', 'external.example.com/typing')
    assert.equal(form.pendingOriginWriteCount(), 1)

    // The External result comes back anyway: it must not touch the field.
    form.handleOriginSettingsResult(acceptedFrame(sent[0].requestId, 'publicBaseUrl', 'https://external.example.com'))
    assert.equal(form.publicBaseUrlInput.value, 'external.example.com/typing')

    // The Share write is untouched by the other field's typing.
    form.handleOriginSettingsResult(acceptedFrame(sent[1].requestId, 'shareBaseUrl', 'https://share.example.com'))
    assert.equal(form.shareBaseUrlInput.value, 'https://share.example.com')
})

test('a result whose field was retyped to another value is dropped', async () => {
    const { form, sent } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    await form.applyOriginSetting('publicBaseUrl')
    // Mutate the input without going through onOriginInputChange, so the
    // pending id survives but the visible text no longer matches.
    form.publicBaseUrlInput.value = 'other.example.com'

    form.handleOriginSettingsResult(acceptedFrame(sent[0].requestId, 'publicBaseUrl', 'https://external.example.com'))
    assert.equal(form.publicBaseUrlInput.value, 'other.example.com')
})

test('a refused send reports that nothing left the browser', async () => {
    const { form } = setup({ sendResult: false })
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')

    await form.applyOriginSetting('publicBaseUrl')

    assert.equal(form.publicBaseUrlError.value, NOT_CONNECTED_ERROR)
    assert.equal(form.pendingOriginWriteCount(), 0)
})

test('a dropped connection reports every in-flight write instead of dropping it', async () => {
    const { form, dataStore } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    form.onOriginInputChange('peerBaseUrl', 'peer.example.com')
    await form.applyOriginSetting('publicBaseUrl')
    await form.applyOriginSetting('peerBaseUrl')

    dataStore.wsConnected = false
    await nextTick()

    assert.equal(form.publicBaseUrlError.value, CONNECTION_LOST_ERROR)
    assert.equal(form.peerBaseUrlError.value, CONNECTION_LOST_ERROR)
    assert.notEqual(CONNECTION_LOST_ERROR, NOT_CONNECTED_ERROR)
    assert.equal(form.shareBaseUrlError.value, '')
    assert.equal(form.pendingOriginWriteCount(), 0)
})

test('a dropped connection leaves a field the user retyped alone', async () => {
    const { form, dataStore } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    await form.applyOriginSetting('publicBaseUrl')
    form.publicBaseUrlInput.value = 'external.example.com/typing'

    dataStore.wsConnected = false
    await nextTick()

    assert.equal(form.publicBaseUrlError.value, '')
})

test('a plain-HTTP Peer address warns, and a no-op Apply keeps the warning', async () => {
    const { form, sent } = setup({ stored: { peerBaseUrl: 'http://peer.example.com' } })
    form.seedOriginField('peerBaseUrl')

    // Exact no-op: the input already holds the stored, usable value.
    await form.applyOriginSetting('peerBaseUrl')

    assert.equal(sent.length, 0, 'a no-op Apply sends nothing')
    assert.equal(form.peerBaseUrlWarning.value, PLAIN_HTTP_WARNING)
    assert.equal(form.peerBaseUrlError.value, '')
})

test('typing clears the Peer warning, and an HTTPS address never raises it', async () => {
    const { form } = setup()
    form.onOriginInputChange('peerBaseUrl', 'http://peer.example.com')
    await form.applyOriginSetting('peerBaseUrl')
    assert.equal(form.peerBaseUrlWarning.value, PLAIN_HTTP_WARNING)

    form.onOriginInputChange('peerBaseUrl', 'https://peer.example.com')
    assert.equal(form.peerBaseUrlWarning.value, '')
    await form.applyOriginSetting('peerBaseUrl')
    assert.equal(form.peerBaseUrlWarning.value, '')
})

test('changing a configured Peer address waits for inline confirmation', async () => {
    const { form, sent } = setup({
        stored: { peerBaseUrl: 'https://old.example.com' },
    })
    form.seedOriginField('peerBaseUrl')
    form.onOriginInputChange('peerBaseUrl', 'https://new.example.com')

    await form.applyOriginSetting('peerBaseUrl')

    assert.equal(form.peerBaseUrlConfirmation.value, true)
    assert.equal(sent.length, 0)

    await form.confirmPeerBaseUrlApply()

    assert.equal(form.peerBaseUrlConfirmation.value, false)
    assert.equal(sent.length, 1)
})

test('cancelling the Peer address confirmation sends no write', async () => {
    const { form, sent } = setup({
        stored: { peerBaseUrl: 'https://old.example.com' },
    })
    form.seedOriginField('peerBaseUrl')
    form.onOriginInputChange('peerBaseUrl', '')

    await form.applyOriginSetting('peerBaseUrl')
    form.cancelPeerBaseUrlApply()

    assert.equal(form.peerBaseUrlConfirmation.value, false)
    assert.equal(sent.length, 0)
})

test('typing clears the Peer address confirmation', async () => {
    const { form, sent } = setup({
        stored: { peerBaseUrl: 'https://old.example.com' },
    })
    form.seedOriginField('peerBaseUrl')
    form.onOriginInputChange('peerBaseUrl', 'https://new.example.com')
    await form.applyOriginSetting('peerBaseUrl')

    form.onOriginInputChange('peerBaseUrl', 'https://other.example.com')

    assert.equal(form.peerBaseUrlConfirmation.value, false)
    assert.equal(sent.length, 0)
})

test('continuing a Peer address change validates the current input again', async () => {
    const { form, sent } = setup({
        stored: { peerBaseUrl: 'https://old.example.com' },
    })
    form.seedOriginField('peerBaseUrl')
    form.onOriginInputChange('peerBaseUrl', 'https://new.example.com')
    await form.applyOriginSetting('peerBaseUrl')
    form.peerBaseUrlInput.value = 'not an origin'

    await form.confirmPeerBaseUrlApply()

    assert.match(form.peerBaseUrlError.value, /hostname or an HTTP\(S\) origin/)
    assert.equal(sent.length, 0)
})

test('setting the first Peer address needs no confirmation', async () => {
    const { form, sent } = setup()
    form.onOriginInputChange('peerBaseUrl', 'https://peer.example.com')

    await form.applyOriginSetting('peerBaseUrl')

    assert.equal(form.peerBaseUrlConfirmation.value, false)
    assert.equal(sent.length, 1)
})

test('the Share host is refused client-side when it matches this app', async () => {
    const { form, sent } = setup({ locationHostname: 'App.Example' })
    form.onOriginInputChange('shareBaseUrl', 'https://app.example')

    await form.applyOriginSetting('shareBaseUrl')

    assert.match(form.shareBaseUrlError.value, /different hostname from this app/)
    assert.equal(sent.length, 0)
})

test('a store broadcast refreshes an untouched field and spares a typed one', async () => {
    const { form, settingsStore } = setup({ stored: { publicBaseUrl: 'https://old.example' } })
    form.seedOriginField('publicBaseUrl')
    form.onOriginInputChange('shareBaseUrl', 'typed.example.com')

    settingsStore.getPublicBaseUrl = 'https://new.example'
    settingsStore.getShareBaseUrl = 'https://broadcast.example'
    await nextTick()

    assert.equal(form.publicBaseUrlInput.value, 'https://new.example')
    assert.equal(form.shareBaseUrlInput.value, 'typed.example.com')
})

test('a broadcast does not settle a correlated write', async () => {
    const { form, settingsStore, sent } = setup()
    form.onOriginInputChange('publicBaseUrl', 'external.example.com')
    await form.applyOriginSetting('publicBaseUrl')

    settingsStore.getPublicBaseUrl = 'https://external.example.com'
    await nextTick()

    assert.equal(form.pendingOriginWriteCount(), 1)
    form.handleOriginSettingsResult(acceptedFrame(sent[0].requestId, 'publicBaseUrl', 'https://external.example.com'))
    assert.equal(form.pendingOriginWriteCount(), 0)
})

test('the Apply icon tracks whether the field differs from the stored value', () => {
    const { form } = setup({ stored: { publicBaseUrl: 'https://app.example' } })
    form.seedOriginField('publicBaseUrl')
    assert.equal(form.publicBaseUrlApplyIcon.value, 'check')

    form.onOriginInputChange('publicBaseUrl', 'other.example.com')
    assert.equal(form.publicBaseUrlApplyIcon.value, 'triangle-exclamation')
})

test('the Peer prefill offers the External address only when it adds something', () => {
    const { form, settingsStore } = setup()
    assert.equal(form.canPrefillPeerBaseUrl.value, false, 'no External address to offer')

    settingsStore.getPublicBaseUrl = 'https://app.example'
    assert.equal(form.canPrefillPeerBaseUrl.value, true)

    form.peerBaseUrlError.value = 'stale'
    form.prefillPeerBaseUrlFromPublic()
    assert.equal(form.peerBaseUrlInput.value, 'https://app.example')
    assert.equal(form.peerBaseUrlError.value, '')
    assert.equal(form.canPrefillPeerBaseUrl.value, false, 'already equal to the External address')
})
