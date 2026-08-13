export function resolveSessionComposerLock({
    hasAnswerablePendingRequest,
    isComputePending,
}) {
    if (hasAnswerablePendingRequest) {
        return {
            locked: true,
            reason: 'Answer the pending request to send',
            presentation: 'paused',
        }
    }
    if (isComputePending) {
        return {
            locked: true,
            reason: 'Wait for session preparation to finish before sending',
            presentation: 'disabled',
        }
    }
    return { locked: false, reason: '', presentation: 'paused' }
}
