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

test('blocks a target provider that cannot receive every peer attachment', async () => {
    const { peerAttachmentCompatibilityError } = await import('./peerMessageContent.js')
    assert.equal(typeof peerAttachmentCompatibilityError, 'function')
    const payload = {
        images: [{ source: { type: 'base64', media_type: 'image/png', data: 'aW1hZ2U=' } }],
        documents: [{ source: { type: 'text', media_type: 'text/plain', data: 'note' } }],
    }

    assert.equal(
        peerAttachmentCompatibilityError(
            payload,
            { acceptedMimeTypes: ['image/png'] },
            'Codex',
        ),
        'Codex cannot receive all attachments in this message. Choose a session using a compatible provider.',
    )
    assert.equal(
        peerAttachmentCompatibilityError(
            payload,
            { acceptedMimeTypes: ['image/png', 'text/plain'] },
            'Claude Code',
        ),
        '',
    )
    assert.equal(
        peerAttachmentCompatibilityError(
            { images: [], documents: [] },
            { acceptedMimeTypes: [] },
            'Codex',
        ),
        '',
    )
})

test('reports a draft attachment failure instead of hiding it', async () => {
    const { addPeerAttachmentsToDraft } = await import('./peerMessageContent.js')
    assert.equal(typeof addPeerAttachmentsToDraft, 'function')
    const payload = {
        images: [{ id: 'image' }],
        documents: [{ id: 'document' }],
    }
    const attempted = []

    const error = await addPeerAttachmentsToDraft(
        payload,
        block => ({ name: block.id }),
        async file => {
            attempted.push(file.name)
            if (file.name === 'document') throw new Error('IndexedDB failed')
        },
    )

    assert.deepEqual(attempted, ['image', 'document'])
    assert.equal(
        error,
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.',
    )

    const success = await addPeerAttachmentsToDraft(
        payload,
        block => ({ name: block.id }),
        async () => {},
    )
    assert.equal(success, '')
})
