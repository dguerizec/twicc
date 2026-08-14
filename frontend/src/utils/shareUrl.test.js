import test from 'node:test'
import assert from 'node:assert/strict'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const URL_PATH = '/share/tok123/'

test('canonical stored HTTP and HTTPS origins build Share URLs', () => {
    for (const [stored, expected] of [
        ['https://share.example.com', 'https://share.example.com/share/tok123/'],
        ['http://share.example.com:3500', 'http://share.example.com:3500/share/tok123/'],
    ]) {
        assert.equal(buildShareUrl(stored, URL_PATH), expected, stored)
    }
})

test('non-canonical stored origins fail closed', () => {
    for (const stored of [
        'share.example.com',
        'share.example.com:8443',
        'share.example.com/',
        'https://share.example.com///',
        '\t share.example.com \r\n',
        '  share.example.com  ',
        '\ufeffshare.example.com',
        '\u001cshare.example.com',
        'Share.Example.COM',
        'https://share.example.com/',
        'HTTPS://SHARE.EXAMPLE.COM',
        'https://share.example.com?x=1',
    ]) {
        assert.equal(normalizeShareBase(stored), '', stored)
        assert.equal(buildShareUrl(stored, URL_PATH), null, stored)
    }
})

test('empty and malformed stored origins keep sharing disabled', () => {
    for (const stored of [
        '',
        '   ',
        'ftp://share.example.com',
        'https://share.example.com/base',
        'https://u:p@share.example.com',
        '://x',
    ]) {
        assert.equal(normalizeShareBase(stored), '', stored)
        assert.equal(buildShareUrl(stored, URL_PATH), null, stored)
    }
})
