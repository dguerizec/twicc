/** Convert the owner-only created_by serializer shape into badge rendering data. */
export function shareCreatorBadge(createdBy) {
    if (createdBy?.kind !== 'agent') return null
    const session = createdBy.session
    if (!session) {
        return { label: 'Agent-created (hidden session)', to: null }
    }
    return {
        label: session.title || session.id,
        to: {
            name: 'session',
            params: { projectId: session.project_id, sessionId: session.id },
        },
    }
}
