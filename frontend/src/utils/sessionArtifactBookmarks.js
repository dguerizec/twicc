/**
 * Resolve the artifact bookmarks visible from a session's real project
 * context. Unlike the sidebar Artifacts mode, a session tab inherits visible
 * bookmarks from shared workspaces and from the global scope.
 *
 * Returned rows are copies carrying a presentation-only `_visibilitySource`
 * field (`local` | `workspace` | `all`).
 *
 * Visibility and presentation are deliberately separate: scope decides
 * whether a bookmark is present, while the tree always groups a visible row by
 * its raw owning project (including a distinct group for each worktree).
 */
export function computeSessionArtifactBookmarks({
    bookmarks,
    projectId,
    mainProjectId,
    projectScopeIds,
    workspaces,
    workspaceContainsProject,
}) {
    if (!projectId || !mainProjectId) return []

    const localProjectIds = new Set(projectScopeIds || [projectId])
    const sharedWorkspaceIds = (workspaces || [])
        .filter(ws => !ws.archived && workspaceContainsProject(ws.id, mainProjectId))
        .map(ws => ws.id)

    const rows = []
    for (const bookmark of Object.values(bookmarks || {})) {
        let visibilitySource = null
        if (localProjectIds.has(bookmark.project_id)) {
            visibilitySource = 'local'
        } else if (bookmark.scope === 'workspace' || bookmark.scope === 'all') {
            const sharesWorkspace = sharedWorkspaceIds.some(workspaceId =>
                workspaceContainsProject(workspaceId, bookmark.project_id)
            )
            if (sharesWorkspace) visibilitySource = 'workspace'
        }

        if (!visibilitySource && bookmark.scope === 'all') {
            visibilitySource = 'all'
        }
        if (!visibilitySource) continue

        rows.push({
            ...bookmark,
            _visibilitySource: visibilitySource,
        })
    }

    return rows.sort((a, b) => {
        if (a.updated_at < b.updated_at) return 1
        if (a.updated_at > b.updated_at) return -1
        return String(a.name || '').localeCompare(String(b.name || ''))
    })
}
