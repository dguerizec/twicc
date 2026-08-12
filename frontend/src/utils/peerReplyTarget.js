/**
 * Choose whether reply-target initialization can use a normal picker candidate
 * or must ask the store's by-id loader for the session.
 */
export function chooseReplyTargetSource(sessionId, candidates) {
    const session = candidates.find(candidate => candidate.id === sessionId)
    if (session) return { kind: 'candidate', session }
    return { kind: 'load', sessionId }
}

/**
 * The delivery picker's non-pagination exclusions. Project list membership and
 * project staleness are deliberately absent: the normal picker can render a
 * worktree or stale-project row when its explicit scope produces that row.
 */
export function isReplyTargetPickerEligible(session, archivedProjectIds) {
    return !!session
        && !session.hidden
        && !session.draft
        && !session.archived
        && !archivedProjectIds.has(session.project_id)
}

/**
 * Restore an eligible hydrated target omitted only by the current page bound.
 * Existing and ineligible targets preserve the exact input array reference.
 */
export function recoverReplyTargetPagination(
    candidates,
    target,
    archivedProjectIds,
    compareSessions,
) {
    if (!isReplyTargetPickerEligible(target, archivedProjectIds)) return candidates
    if (candidates.some(candidate => candidate.id === target.id)) return candidates
    return [...candidates, target].sort(compareSessions)
}
