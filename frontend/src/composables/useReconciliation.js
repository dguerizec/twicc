// frontend/src/composables/useReconciliation.js

import { useDataStore } from '../stores/data'

const MAX_RETRIES = 5

let isReconciling = false
let needsReconcileAfter = false

export function useReconciliation() {
    const store = useDataStore()

    // ═══════════════════════════════════════════════════════════════════════════
    // Public API
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Called when WebSocket reconnects.
     * Orchestrates the reconciliation process with retry logic.
     * @param {string|null} currentProjectId - Current project being viewed
     * @param {string|null} currentSessionId - Current session being viewed
     */
    async function onReconnected(currentProjectId, currentSessionId) {
        if (isReconciling) {
            // A reconciliation is already in progress, mark that we need another one after
            needsReconcileAfter = true
            return
        }

        isReconciling = true
        try {
            await reconcileWithRetry(currentProjectId, currentSessionId)

            // If a reconnection happened while we were reconciling, do it again
            while (needsReconcileAfter) {
                needsReconcileAfter = false
                await reconcileWithRetry(currentProjectId, currentSessionId)
            }
        } finally {
            isReconciling = false
        }

        // Items are now in sync — audit in-flight sends whose error frame
        // may have been lost while the WebSocket was down (send-failure
        // recovery: resolve the delivered ones, surface the lost ones).
        store.auditAllLoadedInflightSends()
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Internal logic
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Retry reconciliation up to MAX_RETRIES times.
     * After all retries, unload any data that still failed to sync.
     */
    async function reconcileWithRetry(currentProjectId, currentSessionId) {
        let lastResult = null

        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            const groupName = attempt === 1 ? 'Reconciliation' : `Reconciliation, try ${attempt}`
            console.group(groupName)

            try {
                lastResult = await reconcile(currentProjectId, currentSessionId)

                if (!lastResult.hasErrors) {
                    console.groupEnd()
                    return // Success, all done
                }

                // Log failures for this attempt
                logFailures(lastResult)
            } finally {
                console.groupEnd()
            }

            // On next attempt, the mtime comparisons will only retry what failed
            // because successful updates changed the local mtime
        }

        // After all retries, unload what still failed (from the LAST attempt only)
        // BUT: don't unload current project/session to avoid showing empty page to user

        console.group('Reconciliation unloading')
        const unloadedProjectIds = new Set()

        // Unload failed projects first (this also unloads all their sessions)
        for (const projectId of lastResult.failedProjectIds) {
            if (projectId === currentProjectId) {
                console.log(`Skipping unload of current project ${projectId} to preserve user view`)
                continue
            }
            store.unloadProject(projectId)
            unloadedProjectIds.add(projectId)
            console.log(`Unloaded project ${projectId}`)
        }

        // Unload failed sessions (skip if their project was already unloaded)
        for (const { projectId, sessionId } of lastResult.failedSessions) {
            if (sessionId === currentSessionId) {
                console.log(`Skipping unload of current session ${sessionId} to preserve user view`)
                continue
            }
            if (unloadedProjectIds.has(projectId)) {
                // Project was already unloaded, which unloaded all its sessions
                continue
            }
            store.unloadSession(sessionId)
            console.log(`Unloaded session ${sessionId}`)
        }
        console.groupEnd()
    }

    /**
     * Log failures from a reconciliation attempt.
     */
    function logFailures(result) {
        if (!result.hasErrors) return

        console.group('Failures')
        for (const projectId of result.failedProjectIds) {
            console.log(`Failed to load sessions for project ${projectId}`)
        }
        for (const { projectId, sessionId } of result.failedSessions) {
            console.log(`Failed to load items for session ${sessionId} of project ${projectId}`)
        }
        console.groupEnd()
    }

    /**
     * Main reconciliation logic.
     * Loads changed data with priority to current view.
     * @returns {Promise<{hasErrors: boolean, failedProjectIds: string[], failedSessions: Array<{projectId, sessionId}>}>}
     */
    async function reconcile(currentProjectId, currentSessionId) {
        let hasErrors = false
        const failedProjectIds = []
        const failedSessions = [] // [{ projectId, sessionId }, ...]

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 1: Load all projects
        // ═══════════════════════════════════════════════════════════════════════
        console.log('Updating projects')
        let changedProjectIds
        try {
            changedProjectIds = await store.loadProjects()
        } catch (error) {
            console.error('Failed to load projects:', error)
            return { hasErrors: true, failedProjectIds: [], failedSessions: [] }
        }

        const currentProjectHasError = currentProjectId && store.didSessionsFailToLoad(currentProjectId)
        const currentSessionHasError = currentSessionId && store.didSessionItemsFailToLoad(currentSessionId)

        // Defensive: always re-check the focused session on reconnect, even when
        // no project mtime changed. A project's mtime can read stale at the
        // instant we poll (watcher lag), and items written during the outage had
        // their WS broadcast dropped — so relying solely on the mtime delta can
        // silently miss the current session. Reloading its project's sessions is
        // cheap and makes the visible conversation the one thing we never leave
        // behind.
        const hasCurrentSession = currentSessionId != null

        if (changedProjectIds.size === 0 && !currentProjectHasError && !currentSessionHasError && !hasCurrentSession) {
            console.log('Nothing to update')
            return { hasErrors: false, failedProjectIds: [], failedSessions: [] }
        }

        const remainingProjectIds = new Set(changedProjectIds)
        const remainingSessions = [] // [{ projectId, sessionId }, ...]

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 2: Priority chain (current project → current session)
        // Also retry if current project/session has a loading error
        // ═══════════════════════════════════════════════════════════════════════
        const currentProjectNeedsUpdate = currentProjectId && (
            remainingProjectIds.has(currentProjectId) || currentProjectHasError || currentSessionHasError || hasCurrentSession
        )

        if (currentProjectNeedsUpdate) {
            const groupName = hasCurrentSession ? 'Current project/session' : 'Current project'
            console.group(groupName)

            try {
                console.log('Updating current project sessions')
                const changedSessionIds = await store.loadSessions(currentProjectId, { force: true })
                remainingProjectIds.delete(currentProjectId)

                // ALWAYS re-check the focused session's items, not only when its
                // mtime differs. The mtime comparison races with the reconnected
                // WebSocket stream: any session_updated received while this
                // reconciliation was in flight (watcher broadcast for a working
                // session, session_viewed echo from the wake-up presence ping…)
                // already refreshed the local mtime to the server value — the
                // session then reads as "unchanged" while the items written
                // during the outage were never loaded. loadNewItems is cheap
                // when there is nothing to do.
                const currentSessionNeedsUpdate = !!currentSessionId
                if (currentSessionNeedsUpdate) {
                    try {
                        console.log('Updating current session')
                        await loadNewItems(currentProjectId, currentSessionId)
                    } catch (error) {
                        console.error(`Failed to load items for current session:`, error)
                        failedSessions.push({ projectId: currentProjectId, sessionId: currentSessionId })
                        hasErrors = true
                    }
                    changedSessionIds.delete(currentSessionId)
                }

                // Other sessions from current project go to parallel batch
                for (const sessionId of changedSessionIds) {
                    remainingSessions.push({ projectId: currentProjectId, sessionId })
                }
            } catch (error) {
                console.error(`Failed to load sessions for current project:`, error)
                failedProjectIds.push(currentProjectId)
                hasErrors = true
            }

            console.groupEnd()
        }

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 3: Remaining projects (in parallel)
        // ═══════════════════════════════════════════════════════════════════════
        if (remainingProjectIds.size > 0) {
            console.group('Updating sessions lists')

            const projectIdsArray = [...remainingProjectIds]
            for (const projectId of projectIdsArray) {
                console.log(`Project ${projectId}`)
            }

            const results = await Promise.allSettled(
                projectIdsArray.map(projectId =>
                    store.loadSessions(projectId, { force: true })
                        .then(changedIds => ({ projectId, changedIds, success: true }))
                        .catch(error => ({ projectId, error, success: false }))
                )
            )

            for (const result of results) {
                if (result.status === 'fulfilled') {
                    const { projectId, changedIds, success } = result.value
                    if (success) {
                        for (const sessionId of changedIds) {
                            remainingSessions.push({ projectId, sessionId })
                        }
                    } else {
                        failedProjectIds.push(projectId)
                        hasErrors = true
                    }
                } else {
                    // Promise itself rejected (shouldn't happen with our .catch, but defensive)
                    hasErrors = true
                }
            }

            console.groupEnd()
        }

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 4: Remaining sessions (in parallel)
        // ═══════════════════════════════════════════════════════════════════════
        if (remainingSessions.length === 0) {
            console.log('No sessions to update')
        } else {
            console.group('Updating sessions')

            for (const { projectId, sessionId } of remainingSessions) {
                console.log(`Session ${sessionId} of project ${projectId}`)
            }

            const results = await Promise.allSettled(
                remainingSessions.map(({ projectId, sessionId }) =>
                    loadNewItems(projectId, sessionId)
                        .then(() => ({ projectId, sessionId, success: true }))
                        .catch(error => ({ projectId, sessionId, error, success: false }))
                )
            )

            for (const result of results) {
                if (result.status === 'fulfilled' && !result.value.success) {
                    const { projectId, sessionId } = result.value
                    failedSessions.push({ projectId, sessionId })
                    hasErrors = true
                }
            }

            console.groupEnd()
        }

        return { hasErrors, failedProjectIds, failedSessions }
    }

    /**
     * Bring a session's items up to date with the server after a disconnect.
     *
     * Delegates gap detection to store.ensureSessionItemsCoverage: it loads the
     * missing tail-window lines (with content) and restores metadata over bare
     * placeholders (holes left by broadcasts lost during the outage) so the
     * scroller's gap-fill can take over. A plain "server last_line vs our last
     * item" check is NOT enough — a live item received right after reconnect
     * extends the items array over the gap, hiding the missing lines.
     *
     * Throws when a fetch failed, so the retry logic re-runs it.
     * Also re-fetches tool states and agent links that may have been missed
     * during the disconnect (these are normally delivered via WS messages).
     */
    async function loadNewItems(projectId, sessionId) {
        const session = store.getSession(sessionId)
        if (!session) return

        const ok = await store.ensureSessionItemsCoverage(sessionId)
        if (!ok) {
            throw new Error(`Failed to load missing items for session ${sessionId}`)
        }

        // Re-fetch tool states and agent links that may have arrived while disconnected.
        // Order matters: fetchSubagentsState reads toolStates to determine if agents are done.
        await store.fetchToolStates(projectId, sessionId)
        await store.fetchSubagentsState(projectId, sessionId)
    }

    return {
        onReconnected,
        // Expose for debugging/testing
        get isReconciling() { return isReconciling }
    }
}
