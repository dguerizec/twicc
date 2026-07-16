// Browser-pane default URL: project-chain resolver. Same walk as the other
// per-project defaults (worktree main repo first, else nearest path ancestor
// — see projectAgentDefaults.js): the first ancestor with a non-empty
// browser_urls list wins, yielding its default entry (or the first one when
// none is flagged). The workspace fallback (first workspace containing the
// project that carries saved URLs) lives in BrowserPane.vue — it needs the
// workspaces store, not the project chain.
import { defaultBrowserUrlEntry } from './browserUrlEntries'
import { ancestorChain } from './projectAgentDefaults'

/**
 * @param {string|null} projectId
 * @param {Object} projectsById - dataStore.projects (id → project row)
 * @returns {(string|null)} the inherited default URL, or null when nothing in
 *   the chain saves one (the caller falls back to workspaces, then blank).
 */
export function resolveProjectBrowserUrl(projectId, projectsById) {
    if (!projectId) return null
    for (const node of ancestorChain(projectId, projectsById)) {
        const entry = defaultBrowserUrlEntry(node.browser_urls)
        if (entry) return entry.url
    }
    return null
}
