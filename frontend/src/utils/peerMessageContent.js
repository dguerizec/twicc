const MARKDOWN_CONFIRM_BYTES = 64 * 1024
const ATTACHMENTS_CONFIRM_BYTES = 1024 * 1024

export function shouldConfirmPeerMarkdown(textBytes) {
    return Number.isFinite(textBytes) && textBytes >= MARKDOWN_CONFIRM_BYTES
}

export function peerAttachmentBytes(metadata) {
    if (!Array.isArray(metadata)) return 0
    return metadata.reduce((total, item) => {
        const bytes = item?.bytes
        return total + (Number.isFinite(bytes) && bytes >= 0 ? bytes : 0)
    }, 0)
}

export function shouldConfirmPeerAttachments(metadata) {
    return peerAttachmentBytes(metadata) >= ATTACHMENTS_CONFIRM_BYTES
}

export function formatPeerContentBytes(bytes) {
    const safeBytes = Number.isFinite(bytes) && bytes > 0 ? bytes : 0
    if (safeBytes >= 1024 * 1024) return `${(safeBytes / (1024 * 1024)).toFixed(1)} MiB`
    if (safeBytes >= 1024) return `${(safeBytes / 1024).toFixed(1)} KiB`
    return `${safeBytes} B`
}

export function mergePeerAttachments(detail, attachments) {
    return {
        ...detail,
        payload: {
            ...(detail?.payload || {}),
            images: Array.isArray(attachments?.images) ? attachments.images : [],
            documents: Array.isArray(attachments?.documents) ? attachments.documents : [],
        },
    }
}

function attachmentBlocks(payload) {
    return [
        ...(Array.isArray(payload?.images) ? payload.images : []),
        ...(Array.isArray(payload?.documents) ? payload.documents : []),
    ]
}

function attachmentMimeType(block) {
    if (block?.source?.type === 'text') return 'text/plain'
    if (block?.source?.type === 'base64') {
        return block.source.media_type || 'application/octet-stream'
    }
    return ''
}

function attachmentMimeTypesAreCompatible(mimeTypes, capabilities) {
    const acceptedMimeTypes = new Set(capabilities?.acceptedMimeTypes || [])
    return mimeTypes.every(mimeType => acceptedMimeTypes.has(mimeType))
}

function peerAttachmentsAreCompatible(payload, capabilities) {
    return attachmentMimeTypesAreCompatible(
        attachmentBlocks(payload).map(attachmentMimeType),
        capabilities,
    )
}

export function peerAttachmentCompatibilityError(payload, capabilities, providerLabel) {
    if (peerAttachmentsAreCompatible(payload, capabilities)) return ''
    return `${providerLabel} cannot receive all attachments in this message. `
        + 'Choose a session using a compatible provider.'
}

export function peerDeliveryTargetState(payload, target, contentReady, missingTargetError = '') {
    if (!target) {
        return { disabled: true, error: contentReady ? missingTargetError : '' }
    }
    const error = peerAttachmentCompatibilityError(
        payload,
        target.capabilities,
        target.providerLabel,
    )
    return { disabled: !contentReady || Boolean(error), error }
}

export function firstCompatiblePeerProvider(payload, providers) {
    return providers.find(candidate =>
        peerAttachmentsAreCompatible(payload, candidate.capabilities),
    )?.provider ?? null
}

export function firstCompatiblePeerProviderForMetadata(metadata, providers) {
    const mimeTypes = (Array.isArray(metadata) ? metadata : [])
        .map(item => item?.media_type || '')
    return providers.find(candidate =>
        attachmentMimeTypesAreCompatible(mimeTypes, candidate.capabilities),
    )?.provider ?? null
}

export async function addPeerAttachmentsToDraft(payload, blockToFile, addAttachment) {
    for (const [index, block] of attachmentBlocks(payload).entries()) {
        try {
            const file = blockToFile(block, index)
            if (!file) throw new Error('Invalid Peer attachment')
            await addAttachment(file)
        } catch {
            return 'TwiCC could not add all attachments to the draft. '
                + 'The Peer message is still available for delivery to another session.'
        }
    }
    return ''
}

export function peerContentAllowsDelivery(detailReady, markdownState, attachmentsState) {
    return detailReady && markdownState === 'ready' && attachmentsState === 'ready'
}
