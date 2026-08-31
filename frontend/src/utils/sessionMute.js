/**
 * Persist one session's finished-working notification preference.
 *
 * @param {Object<string, object>} sessions
 * @param {Function} apiFetch
 * @param {string} projectId
 * @param {string} sessionId
 * @param {boolean} value
 * @throws {Error} If the update fails
 */
export async function applySessionMuteOnUserTurn(sessions, apiFetch, projectId, sessionId, value) {
    const session = sessions[sessionId]
    const oldValue = session?.mute_on_user_turn

    if (session) {
        session.mute_on_user_turn = value
    }

    try {
        const response = await apiFetch(
            `/api/projects/${projectId}/sessions/${sessionId}/`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mute_on_user_turn: value }),
            },
        )
        if (!response.ok) {
            const data = await response.json()
            throw new Error(data.error || 'Failed to update session notifications')
        }
        const updatedSession = await response.json()
        sessions[sessionId] = { ...sessions[sessionId], ...updatedSession }
    } catch (error) {
        if (session && oldValue !== undefined) {
            session.mute_on_user_turn = oldValue
        }
        throw error
    }
}
