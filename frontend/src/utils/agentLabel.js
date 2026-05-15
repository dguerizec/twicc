/**
 * Subagent-tab labelling helpers.
 *
 * The session tab UI (``SessionView``, ``SessionHeader``) labels each
 * subagent tab with a short human-readable string. When the underlying
 * ``Session`` row carries a provider-supplied ``slug`` (e.g. Codex's
 * ``agent_nickname`` like ``Chandrasekhar``), prefer that; otherwise
 * fall back to the first 8 characters of the agent's session id.
 *
 * The data store is passed in rather than imported here to keep this
 * module free of Pinia / store cycles (utility imported from views,
 * components, and possibly composables).
 */

const SHORT_ID_LENGTH = 8

/**
 * Truncated agent id used as a fallback when no slug is available.
 */
export function getAgentShortId(agentId) {
    if (!agentId) return ''
    return agentId.substring(0, SHORT_ID_LENGTH)
}

/**
 * Best label for a subagent tab: the session slug when present, the
 * truncated id otherwise. ``dataStore`` is the Pinia ``useDataStore``
 * instance — passed by the caller to avoid a circular import.
 */
export function getAgentDisplayLabel(agentId, dataStore) {
    const sess = dataStore?.getSession?.(agentId)
    return sess?.slug || getAgentShortId(agentId)
}
