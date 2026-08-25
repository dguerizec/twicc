import { ref } from 'vue'
import { useDataStore } from '../stores/data'
import { apiFetch } from '../utils/api'

/**
 * useProjectDirectoryRecheck - the action-time "is that directory back?" call.
 *
 * `Project.stale` is a STORED observation: nothing watches the working
 * directories, so a folder restored while TwiCC runs stays flagged until this
 * runs (or a restart). The backend re-stats the directory, re-resolves
 * `git_root` when it is back, and broadcasts `project_updated` on change — but
 * only on change, so the response is applied to the store directly: the caller
 * must reflect the check that just ran whatever the WS state.
 *
 * Shared by the project edit dialog and ProjectMissingDirectoryDialog.
 *
 * @returns {{busy: import('vue').Ref<boolean>, outcome: import('vue').Ref<string>,
 *            recheck: (projectId: string) => Promise<Object|null>, reset: () => void}}
 *   `outcome` is '' (nothing tried, or the directory is back), 'confirmed' (the
 *   check ran and the directory is still gone) or 'error' (the check failed).
 */
export function useProjectDirectoryRecheck() {
    const store = useDataStore()
    const busy = ref(false)
    const outcome = ref('')

    function reset() {
        outcome.value = ''
    }

    async function recheck(projectId) {
        if (!projectId || busy.value) return null
        busy.value = true
        outcome.value = ''
        let response
        try {
            response = await apiFetch(`/api/projects/${projectId}/refresh-directory/`, { method: 'POST' })
        } catch (error) {
            outcome.value = 'error'
            busy.value = false
            return null
        }
        if (!response.ok) {
            outcome.value = 'error'
            busy.value = false
            return null
        }
        const updated = await response.json()
        store.updateProject(updated)
        outcome.value = updated.stale ? 'confirmed' : ''
        busy.value = false
        return updated
    }

    return { busy, outcome, recheck, reset }
}
