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

export function peerContentAllowsDelivery(detailReady, markdownState, attachmentsState) {
    return detailReady && markdownState === 'ready' && attachmentsState === 'ready'
}
