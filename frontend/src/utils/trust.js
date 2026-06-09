// frontend/src/utils/trust.js
//
// Front-side mirror of the backend trust resolver (twicc/trust.py §4). Given the
// projects already in the store, compute a project's *effective* trust without a
// backend round-trip when it can be resolved locally. The backend stays the
// authority — when this returns 'unknown' the caller asks the backend (which may
// seed from the provider configs), then prompts the user if still unknown.
//
// Resolution order (worktree-first ∪ path-prefix, no git boundary):
//   1. trust non-null            → that value (self is sovereign)
//   2. worktree (worktree_of set)→ inherit the main repo's value if it propagates
//   3. else                      → inherit the nearest explicit path ancestor if it propagates
//   4. nothing                   → unknown
//
// Each project carries: { id, directory, git_root, worktree_of, trust, trust_propagation }.
// `trust` is true / false / null; `worktree_of` is the main repo's project id or null.

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

function nearestExplicitPathAncestor(project, projectsById) {
    let best = null
    let bestLen = -1
    for (const id in projectsById) {
        const q = projectsById[id]
        if (q.id === project.id || q.trust == null || !q.directory) continue
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

// Internal: returns { state, propagates, sourceId, via }. `propagates` is whether
// this node's resolution cascades to its own inherited descendants.
function resolve(id, projectsById, seen) {
    const p = projectsById[id]
    if (!p) return { state: null, propagates: false, sourceId: null, via: 'unresolved' }

    if (p.trust != null) {
        return { state: p.trust, propagates: !!p.trust_propagation, sourceId: id, via: 'self' }
    }
    if (seen.has(id)) {
        return { state: null, propagates: false, sourceId: null, via: 'unresolved' }
    }
    seen.add(id)

    let source
    let via
    if (p.worktree_of) {
        source = projectsById[p.worktree_of]
        via = 'worktree'
    } else if (p.directory) {
        source = nearestExplicitPathAncestor(p, projectsById)
        via = 'path'
    }
    if (!source) {
        return { state: null, propagates: false, sourceId: null, via: 'unresolved' }
    }

    const r = resolve(source.id, projectsById, seen)
    if (r.propagates && r.state != null) {
        return { state: r.state, propagates: true, sourceId: r.sourceId, via }
    }
    return { state: null, propagates: false, sourceId: null, via: 'unresolved' }
}

/**
 * Resolve a project's effective trust from the projects already in the store.
 * @returns {{ state: (boolean|null), sourceId: (string|null), via: string }}
 *   state: true = trusted, false = untrusted, null = unknown (ask the backend).
 */
export function resolveProjectTrust(projectId, projectsById) {
    const r = resolve(projectId, projectsById, new Set())
    return { state: r.state, sourceId: r.sourceId, via: r.via }
}

/**
 * Default value for the "propagate" choice when the user makes a trust decision:
 * on when the project is under git (matches the backend `resolve_git_from_path`
 * heuristic, read here from the serialized `git_root`).
 */
export function defaultTrustPropagation(project) {
    return !!(project && project.git_root)
}
