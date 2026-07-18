// frontend/src/utils/projectIcon.js
//
// Resolve a project's EFFECTIVE icon URL. An icon is a per-project value that
// cascades to descendants by inheritance — the same chain used for agent
// defaults (worktree main repo first, else nearest path ancestor, recursively).
// So setting an icon on a project shows it on every descendant that doesn't
// override, with the auto-discovered repo icon as the socle.
//
// The backend exposes two bricks per project (query-free serializer):
//   - icon           : "inherit" | "none" | "<token>" (this project's state)
//   - icon_override_url : this project's OWN manual icon URL, or null
//   - repo_icon_url     : the auto-discovered repo icon for its anchor, or null
// The chain walk (which needs sibling/ancestor rows) is done here, client-side.
import { ancestorChain } from './projectAgentDefaults'

/**
 * @param {string} projectId
 * @param {Record<string, object>} projectsById  the store's projects map
 * @returns {string|null} the effective icon URL, or null (render the color dot)
 */
export function resolveProjectIconUrl(projectId, projectsById) {
    const chain = ancestorChain(projectId, projectsById)
    for (const node of chain) {
        const state = node.icon || 'inherit'
        if (state === 'none') return null                       // explicit color, stops here
        if (state !== 'inherit') return node.icon_override_url || null  // own override, cascades
    }
    // Nobody in the chain chose explicitly → this project's auto repo icon.
    return projectsById[projectId]?.repo_icon_url || null
}
