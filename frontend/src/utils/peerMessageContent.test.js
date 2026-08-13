import test from 'node:test'
import assert from 'node:assert/strict'

import {
    formatPeerContentBytes,
    mergePeerAttachments,
    peerAttachmentBytes,
    peerContentAllowsDelivery,
    shouldConfirmPeerAttachments,
    shouldConfirmPeerMarkdown,
} from './peerMessageContent.js'

test('requires confirmation at the 64 KiB markdown boundary', () => {
    assert.equal(shouldConfirmPeerMarkdown(64 * 1024 - 1), false)
    assert.equal(shouldConfirmPeerMarkdown(64 * 1024), true)
})

test('requires confirmation at the 1 MiB total attachment boundary', () => {
    const below = [{ bytes: 512 * 1024 }, { bytes: 512 * 1024 - 1 }]
    const boundary = [...below, { bytes: 1 }]

    assert.equal(peerAttachmentBytes(below), 1024 * 1024 - 1)
    assert.equal(shouldConfirmPeerAttachments(below), false)
    assert.equal(peerAttachmentBytes(boundary), 1024 * 1024)
    assert.equal(shouldConfirmPeerAttachments(boundary), true)
})

test('ignores malformed attachment sizes', () => {
    const metadata = [{ bytes: -1 }, { bytes: '12' }, {}, null, { bytes: 7 }]

    assert.equal(peerAttachmentBytes(metadata), 7)
})

test('formats content sizes with binary units', () => {
    assert.equal(formatPeerContentBytes(141_846), '138.5 KiB')
    assert.equal(formatPeerContentBytes(5 * 1024 * 1024), '5.0 MiB')
})

test('merges attachment blocks without mutating the lightweight detail', () => {
    const detail = {
        id: 1,
        payload: { text: 'message', images: [], documents: [] },
    }
    const attachments = {
        images: [{ type: 'image' }],
        documents: [{ type: 'document' }],
    }

    const merged = mergePeerAttachments(detail, attachments)

    assert.notStrictEqual(merged, detail)
    assert.notStrictEqual(merged.payload, detail.payload)
    assert.deepEqual(merged.payload, {
        text: 'message',
        images: attachments.images,
        documents: attachments.documents,
    })
    assert.deepEqual(detail.payload, { text: 'message', images: [], documents: [] })
})

test('allows delivery only after detail, markdown, and attachments are ready', () => {
    assert.equal(peerContentAllowsDelivery(true, 'ready', 'ready'), true)
    for (const [detailReady, markdownState, attachmentsState] of [
        [false, 'ready', 'ready'],
        [true, 'loading', 'ready'],
        [true, 'confirm', 'ready'],
        [true, 'declined', 'ready'],
        [true, 'error', 'ready'],
        [true, 'ready', 'loading'],
        [true, 'ready', 'confirm'],
        [true, 'ready', 'declined'],
        [true, 'ready', 'error'],
    ]) {
        assert.equal(
            peerContentAllowsDelivery(detailReady, markdownState, attachmentsState),
            false,
        )
    }
})
