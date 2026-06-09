// frontend/src/utils/projectAgentDefaults.js
//
// Front-side mirror of the backend per-project agent-settings resolver
// (twicc/project_hierarchy.py). Given the projects already in the store, walk a
// project's ancestor chain (worktree main repo first, else nearest path
// ancestor, recursively) and resolve — field by field — the inherited
// agent-settings defaults and the inherited default provider. The backend stays
// the authority for what actually runs (it re-resolves the same chain when a
// session is created/resumed); this mirror exists so the UI can SHOW the
// resolved defaults (the popover baseline, diff-marking and reset target) and
// pre-select a new draft's provider without a round-trip.
//
// Unlike trust (utils/trust.js) there is NO propagation gate: every ancestor is
// visited and, for each field, the first non-null value wins. The chain is also
// UNFILTERED — an ancestor with no own defaults is still a step up, because a
// different field may be set higher.
//
// Each project carries: { id, directory, worktree_of, default_provider,
// default_agent_settings }. `default_agent_settings` is
// { <provider>: { <AgentSettings field>: value|null } } or null; field names
// are the canonical wire names (selected_model, thinking_enabled, effort,
// permission_mode, context_max, claude_in_chrome, fast_mode).

function segments(path) {
    return path.split('/').filter(Boolean)
}

// True iff `ancestor` is a strict directory prefix of `descendant`, compared
// segment-by-segment (so /a/b is NOT an ancestor of /a/bc). Both are realpath'd.
function isPathAncestor(ancestor, descendant) {
    const a = segments(ancestor)
    const d = segments(descendant)
    if (a.length >= d.length) return false
    for (let i = 0; i < a.length; i++) {
        if (a[i] !== d[i]) return false
    }
    return true
}

// Nearest (longest-path) ancestor — UNFILTERED: any registered project that is a
// strict path-prefix qualifies, regardless of whether it has its own defaults.
function nearestPathAncestor(project, projectsById) {
    let best = null
    let bestLen = -1
    for (const id in projectsById) {
        const q = projectsById[id]
        if (q.id === project.id || !q.directory) continue
        if (isPathAncestor(q.directory, project.directory)) {
            const depth = segments(q.directory).length
            if (depth > bestLen) {
                best = q
                bestLen = depth
            }
        }
    }
    return best
}

/**
 * Ordered ancestor chain [self, parent, grandparent, ...]. At each node the
 * parent is its `worktree_of` main repo if set, else its nearest path ancestor.
 * Cycle-guarded against pathological `worktree_of` loops.
 */
export function ancestorChain(projectId, projectsById) {
    const chain = []
    const seen = new Set()
    let node = projectsById[projectId]
    while (node && !seen.has(node.id)) {
        chain.push(node)
        seen.add(node.id)
        if (node.worktree_of) {
            node = projectsById[node.worktree_of]
        } else if (node.directory) {
            node = nearestPathAncestor(node, projectsById)
        } else {
            node = null
        }
    }
    return chain
}

/**
 * Resolve the inherited per-project agent-settings defaults for a provider.
 * @returns {Object} a { field: value } map holding only the fields the chain
 *   actually sets; a missing field means "inherit further / fall back to the
 *   global default" (the caller applies that global fallback).
 */
export function resolveProjectAgentDefaults(projectId, provider, projectsById) {
    const resolved = {}
    if (!projectId || !provider) return resolved
    for (const node of ancestorChain(projectId, projectsById)) {
        const bundle = (node.default_agent_settings && node.default_agent_settings[provider]) || {}
        for (const field in bundle) {
            const value = bundle[field]
            // First non-null wins (nearest ancestor); an explicit null means
            // "inherit", so it does not block a value set further up.
            if (value != null && !(field in resolved)) {
                resolved[field] = value
            }
        }
    }
    return resolved
}

/**
 * Resolve the inherited default provider for a project (worktree/path chain).
 * @returns {(string|null)} a provider value, or null when nothing in the chain
 *   sets one (the caller falls back to the global default provider).
 */
export function resolveProjectDefaultProvider(projectId, projectsById) {
    if (!projectId) return null
    for (const node of ancestorChain(projectId, projectsById)) {
        if (node.default_provider) return node.default_provider
    }
    return null
}
