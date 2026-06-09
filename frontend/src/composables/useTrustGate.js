// frontend/src/composables/useTrustGate.js
//
// Global gate run before starting a new session in a project: make sure the
// project's trust is settled (or recorded) first. A single ProjectTrustDialog is
// mounted at the app root and registered here; every "new session" entry point
// calls `ensureProjectTrust(projectId)` and proceeds only when it resolves true.
//
// Flow (docs/plans/2026-06-09-project-trust-design.md §5):
//   1. resolve locally from the store (no round-trip when already resolved)
//   2. else ask the backend (it may seed from the provider configs)
//   3. else prompt the user; persist the decision via /trust/decide/
// On any error we proceed (the gate must never hard-block starting a session;
// the project simply stays unresolved and is asked again next time).

import { apiFetch } from '../utils/api'
import { resolveProjectTrust } from '../utils/trust'
import { useDataStore } from '../stores/data'

// Module-level singleton, set by App.vue once the dialog is mounted.
let trustDialog = null

export function registerTrustDialog(instance) {
    trustDialog = instance
}

/**
 * Ensure a project's trust is settled before a new session is created in it.
 * @param {string} projectId
 * @returns {Promise<boolean>} true → proceed, false → abort (user cancelled).
 */
export async function ensureProjectTrust(projectId) {
    if (!projectId) return true
    const store = useDataStore()

    // 1. Resolve locally from the projects we already have.
    if (resolveProjectTrust(projectId, store.projects).state != null) return true

    // 2. Ask the backend (it may seed from the provider configs and persist).
    try {
        const res = await apiFetch(`/api/projects/${projectId}/trust/resolve/`, { method: 'POST' })
        if (res.ok) {
            const data = await res.json()
            if (data.state != null) return true
        }
    } catch (err) {
        console.warn('Trust resolve failed; proceeding without gate', err)
        return true
    }

    // 3. Still unknown → prompt the user.
    const project = store.getProject(projectId)
    if (!trustDialog || !project) return true
    const decision = await trustDialog.requestDecision(project)
    if (!decision) return false // cancelled → abort the new session

    try {
        await apiFetch(`/api/projects/${projectId}/trust/decide/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                trusted: decision.trusted,
                propagation: decision.propagation,
            }),
        })
    } catch (err) {
        // The decision didn't persist, but the user expressed intent — proceed.
        console.warn('Trust decide failed', err)
    }
    return true
}
