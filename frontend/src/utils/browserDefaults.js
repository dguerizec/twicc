// Browser-pane default URL: project-chain resolver. Same walk as the other
// per-project defaults (worktree main repo first, else nearest path ancestor
// — see projectAgentDefaults.js): the first ancestor with an own
// default_browser_url wins. The workspace fallback (first workspace containing
// the project that carries a browserUrl) lives in BrowserPane.vue — it needs
// the workspaces store, not the project chain.
import { ancestorChain } from './projectAgentDefaults'

/**
 * @param {string|null} projectId
 * @param {Object} projectsById - dataStore.projects (id → project row)
 * @returns {(string|null)} the inherited default URL, or null when nothing in
 *   the chain sets one (the caller falls back to workspaces, then blank).
 */
export function resolveProjectBrowserUrl(projectId, projectsById) {
    if (!projectId) return null
    for (const node of ancestorChain(projectId, projectsById)) {
        if (node.default_browser_url) return node.default_browser_url
    }
    return null
}
