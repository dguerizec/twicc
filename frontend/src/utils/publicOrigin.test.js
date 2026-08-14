import test from 'node:test'
import assert from 'node:assert/strict'

import {
    checkPublicOriginInput,
    isRecognizablyCanonicalPublicOrigin,
    usablePublicOrigin,
} from './publicOrigin.js'

test('the form check keeps only the safe hard rejections', () => {
    for (const [input, error] of [
        [42, 'type'],
        ['//example.com', 'scheme'],
        ['ftp://example.com', 'scheme'],
        ['https://', 'host'],
        ['https://user:secret@example.com', 'credentials'],
        ['https://exämple.com', 'host'],
        ['https://%65xample.com', 'host'],
        ['https://exa\tmple.com', 'host'],
        ['https://exa\nmple.com', 'host'],
        ['https://exa\rmple.com', 'host'],
        ['https://example.com:', 'port'],
    ]) {
        assert.equal(checkPublicOriginInput(input).error, error, String(input))
    }
})

test('the form check defers backend-only verdicts without rewriting input', () => {
    for (const input of [
        'https://a..example',
        'https://xn--e28h.example',
        'https://[xyz]',
        'https://example.com:bad',
        'https://example.com/base',
        'https://example.com?x=1',
        'https://example.com#part',
        `https://example.com:${'0'.repeat(5000)}`,
    ]) {
        assert.deepEqual(
            { value: checkPublicOriginInput(input).value, error: checkPublicOriginInput(input).error },
            { value: input, error: null },
            input,
        )
    }
})

test('port zero and the normative outer trim remain valid', () => {
    assert.equal(checkPublicOriginInput('  https://example.com:0\r\n').value, 'https://example.com:0')
})

test('the browser hostname is only an optional hint', () => {
    assert.equal(checkPublicOriginInput('HTTPS://APP.EXAMPLE').hostname, 'app.example')
    assert.equal(checkPublicOriginInput('https://[xyz]').hostname, null)
})

test('stored consumers accept recognizable canonical backend output', () => {
    for (const value of [
        'https://example.com',
        'http://localhost:3501',
        'https://192.168.1.42:8443',
        'https://[::1]:8443',
        'https://[::ffff:1.2.3.4]',
        'https://xn--fa-hia.de',
        'https://example.com:0',
    ]) {
        assert.equal(usablePublicOrigin(value), value, value)
    }
})

test('stored consumers fail closed for non-canonical or malformed text', () => {
    for (const value of [
        'HTTPS://EXAMPLE.COM',
        'https://example.com/',
        'https://example.com:443',
        'https://example.com/base',
        'https://example.com.',
        'https://a..example',
        'https://my_host.example',
        'https://192.168.001.1',
        'https://[0:0:0:0:0:0:0:1]',
        'https://%65xample.com',
        'ftp://example.com',
    ]) {
        assert.equal(usablePublicOrigin(value), '', value)
    }
})

test('the stored guard is separate from the permissive form check', () => {
    for (const value of [
        'https://a..example',
        'https://example.com/base',
        'https://example.com:bad',
        'https://[xyz]',
    ]) {
        assert.equal(checkPublicOriginInput(value).error, null, value)
        assert.equal(isRecognizablyCanonicalPublicOrigin(value), false, value)
    }
})
