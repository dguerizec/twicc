import test from 'node:test'
import assert from 'node:assert/strict'

import {
    PUBLIC_ORIGIN_ERROR,
    discardOriginSettingWrites,
    originSettingErrorMessage,
    publicOriginErrorMessage,
    refreshOriginInput,
    resolveOriginSettingResult,
    validateOriginSetting,
} from './originSettingsForm.js'

const TWO_INVALID = {
    publicBaseUrl: 'ftp://app.example',
    shareBaseUrl: 'ftp://share.example',
    peerBaseUrl: '',
}

test('two invalid stored origins can be repaired in either order', () => {
    for (const [firstField, firstValue, secondField, secondValue] of [
        ['publicBaseUrl', 'https://app.example', 'shareBaseUrl', 'https://share.example'],
        ['shareBaseUrl', 'https://share.example', 'publicBaseUrl', 'https://app.example'],
    ]) {
        const first = validateOriginSetting({
            field: firstField, input: firstValue, stored: TWO_INVALID, locationHostname: 'localhost',
        })
        assert.deepEqual(first.errors, [])
        assert.deepEqual(first.patch, { [firstField]: firstValue })
        const second = validateOriginSetting({
            field: secondField,
            input: secondValue,
            stored: { ...TWO_INVALID, ...first.patch },
            locationHostname: 'localhost',
        })
        assert.deepEqual(second.errors, [])
        assert.deepEqual(second.patch, { [secondField]: secondValue })
    }
})

test('frontend defers relationship conflicts to the backend', () => {
    const result = validateOriginSetting({
        field: 'peerBaseUrl',
        input: 'http://x.example',
        stored: { publicBaseUrl: 'https://x.example', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.errors, [])
    assert.deepEqual(result.patch, { peerBaseUrl: 'http://x.example' })
})

test('frontend defers structural rules outside its safe subset', () => {
    for (const input of [
        'https://a..example',
        'https://example.com:bad',
        'https://example.com/base',
        'https://[xyz]',
        'https://xn--e28h.example',
    ]) {
        const result = validateOriginSetting({
            field: 'peerBaseUrl',
            input,
            stored: TWO_INVALID,
            locationHostname: 'localhost',
        })
        assert.deepEqual(result.errors, [], input)
        assert.deepEqual(result.patch, { peerBaseUrl: input }, input)
    }
})

test('an unchanged retained invalid origin keeps its visible error after Apply', () => {
    const result = validateOriginSetting({
        field: 'publicBaseUrl',
        input: 'https://a..example',
        stored: {
            publicBaseUrl: 'https://a..example',
            shareBaseUrl: '',
            peerBaseUrl: '',
        },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.patch, {})
    assert.deepEqual(result.errors, [{
        field: 'publicBaseUrl', code: 'retained_stored_value',
    }])
    const message = originSettingErrorMessage(result.errors, 'publicBaseUrl', publicOriginErrorMessage)
    // The retained value is syntactically fine for the frontend subset, so the
    // generic structural copy would describe the wrong defect.
    assert.notEqual(message, PUBLIC_ORIGIN_ERROR)
    assert.equal(message, 'The stored address is not valid. Change it, then apply again.')
})

test('each origin error code maps to its own message', () => {
    assert.equal(publicOriginErrorMessage('invalid_origin_scheme'), 'The address must use HTTP or HTTPS.')
    assert.equal(publicOriginErrorMessage('retained_stored_value'),
        'The stored address is not valid. Change it, then apply again.')
    assert.equal(publicOriginErrorMessage('origin_conflict_ambiguous_authority'),
        'The Peer and External addresses must be the same origin or use different authorities.')
    assert.equal(publicOriginErrorMessage('invalid_origin_host'), PUBLIC_ORIGIN_ERROR)
    assert.equal(publicOriginErrorMessage(undefined), PUBLIC_ORIGIN_ERROR)
})

test('only the applied field errors appear in the active section', () => {
    const message = originSettingErrorMessage([
        { field: 'peerBaseUrl', code: 'first', message: 'First message.' },
        { field: 'peerBaseUrl', code: 'second', message: 'Second message.' },
        { field: 'publicBaseUrl', code: 'other', message: 'Hidden message.' },
    ], 'peerBaseUrl', code => code)
    assert.equal(message, 'First message. Second message.')
})

test('authoritative refresh preserves typed input after a stale resync', () => {
    assert.equal(refreshOriginInput('https://typed.example', 'https://old.example', 'https://remote.example'),
        'https://typed.example')
})

test('authoritative refresh follows remote state when the input is untouched', () => {
    assert.equal(refreshOriginInput('https://old.example', 'https://old.example', 'https://remote.example'),
        'https://remote.example')
})

test('correlated acceptances expose corrections and accepted no-change values', () => {
    const pending = new Map([
        ['corrected', { field: 'publicBaseUrl', input: 'HTTPS://APP.EXAMPLE:443/' }],
        ['unchanged', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'corrected',
        status: 'accepted',
        settings: { publicBaseUrl: 'https://app.example' },
        errors: [],
    }, 'HTTPS://APP.EXAMPLE:443/'), {
        field: 'publicBaseUrl', status: 'accepted', value: 'https://app.example', errors: [],
    })
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'unchanged',
        status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' },
        errors: [],
    }, 'https://share.example'), {
        field: 'shareBaseUrl', status: 'accepted', value: 'https://share.example', errors: [],
    })
    assert.equal(pending.size, 0)
})

test('outer-trimmed Apply correlates by visible text and adopts the canonical value', () => {
    const visibleInput = '  HTTPS://PEER.EXAMPLE:443/\r\n'
    const prepared = validateOriginSetting({
        field: 'peerBaseUrl',
        input: visibleInput,
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(prepared.patch, { peerBaseUrl: 'HTTPS://PEER.EXAMPLE:443/' })
    const pending = new Map([
        ['trimmed', { field: 'peerBaseUrl', input: visibleInput }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'trimmed',
        status: 'accepted',
        settings: { peerBaseUrl: 'https://peer.example' },
        errors: [],
    }, visibleInput), {
        field: 'peerBaseUrl', status: 'accepted', value: 'https://peer.example', errors: [],
    })
    assert.equal(pending.size, 0)
})

test('a rejection result still shows the applied-field error in either frame-handling order', () => {
    for (const resyncFirst of [true, false]) {
        const submitted = 'http://x.example'
        let input = submitted
        const pending = new Map([
            ['rejected', { field: 'peerBaseUrl', input: submitted }],
        ])
        const payload = {
            request_id: 'rejected',
            status: 'rejected',
            settings: { peerBaseUrl: 'https://old.example' },
            errors: [
                { field: 'peerBaseUrl', message: 'The Peer and External addresses must be the same origin or use different authorities.' },
                { field: 'publicBaseUrl', message: 'Hidden symmetric copy.' },
            ],
        }
        if (resyncFirst) {
            input = refreshOriginInput(input, 'https://old.example', 'https://old.example')
        }
        const result = resolveOriginSettingResult(pending, payload, input)
        if (!resyncFirst) {
            input = refreshOriginInput(input, 'https://old.example', 'https://old.example')
        }
        const message = originSettingErrorMessage(result.errors, result.field, code => code)
        assert.equal(message, 'The Peer and External addresses must be the same origin or use different authorities.')
        assert.equal(input, submitted)
    }
})

test('a stale-version result resolves its write without erasing typed text', () => {
    const pending = new Map([
        ['stale', { field: 'peerBaseUrl', input: 'https://typed.example' }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'stale',
        status: 'rejected',
        settings: { peerBaseUrl: 'https://remote.example' },
        errors: [],
    }, 'https://typed.example'), {
        field: 'peerBaseUrl', status: 'rejected', value: null, errors: [],
    })
    assert.equal(pending.size, 0)
})

test('back-to-back verdicts resolve only their matching writes', () => {
    const pending = new Map([
        ['public-write', { field: 'publicBaseUrl', input: 'https://app.example' }],
        ['share-write', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    const share = resolveOriginSettingResult(pending, {
        request_id: 'share-write', status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' }, errors: [],
    }, 'https://share.example')
    const external = resolveOriginSettingResult(pending, {
        request_id: 'public-write', status: 'accepted',
        settings: { publicBaseUrl: 'https://app.example' }, errors: [],
    }, 'https://app.example')
    assert.equal(share.field, 'shareBaseUrl')
    assert.equal(external.field, 'publicBaseUrl')
    assert.equal(pending.size, 0)
})

test('typing supersedes the same-field write without discarding another field', () => {
    const pending = new Map([
        ['old-peer', { field: 'peerBaseUrl', input: 'https://old.example' }],
        ['share', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    discardOriginSettingWrites(pending, 'peerBaseUrl')
    assert.equal(pending.has('old-peer'), false)
    assert.equal(pending.has('share'), true)
    assert.equal(pending.size, 1)
    pending.set('new-peer', { field: 'peerBaseUrl', input: 'https://new.example' })

    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'old-peer', status: 'accepted',
        settings: { peerBaseUrl: 'https://old.example' }, errors: [],
    }, 'https://new.example'), null)
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'share', status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' }, errors: [],
    }, 'https://share.example').field, 'shareBaseUrl')
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'new-peer', status: 'accepted',
        settings: { peerBaseUrl: 'https://new.example' }, errors: [],
    }, 'https://new.example').field, 'peerBaseUrl')
    assert.equal(pending.size, 0)
})

test('a verdict cannot affect text entered after its Apply', () => {
    const pending = new Map([
        ['old-write', { field: 'peerBaseUrl', input: 'https://old.example' }],
    ])
    discardOriginSettingWrites(pending, 'peerBaseUrl')
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'old-write', status: 'accepted',
        settings: { peerBaseUrl: 'https://old.example' }, errors: [],
    }, 'https://new.example'), null)
})

test('the Share field retains its active-location rule', () => {
    const result = validateOriginSetting({
        field: 'shareBaseUrl',
        input: 'https://APP.example',
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'app.example',
    })
    assert.deepEqual(result.errors, [{ field: 'shareBaseUrl', code: 'location_hostname' }])
    assert.deepEqual(result.patch, {})
})

test('plain HTTP warns only for Peer and still creates the raw patch', () => {
    const result = validateOriginSetting({
        field: 'peerBaseUrl',
        input: '  http://Peer.Example/  ',
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.equal(result.warning, 'http')
    assert.deepEqual(result.patch, { peerBaseUrl: 'http://Peer.Example/' })
})

test('the backend owns canonical equality', () => {
    const result = validateOriginSetting({
        field: 'publicBaseUrl',
        input: 'HTTPS://APP.EXAMPLE:443/',
        stored: { publicBaseUrl: 'https://app.example', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.errors, [])
    assert.deepEqual(result.patch, { publicBaseUrl: 'HTTPS://APP.EXAMPLE:443/' })
})
