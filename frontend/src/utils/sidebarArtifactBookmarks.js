// Scope resolution for the artifact bookmark list, mirroring the session-pin
// rules in computeSidebarSessionBlocks. ALL_PROJECTS_ID is exported by the data
// store; isWorkspaceProjectId by workspaceIds (same modules sidebarSessions.js
// imports from).
import { ALL_PROJECTS_ID } from '../stores/data'
import { isWorkspaceProjectId } from './workspaceIds'

/**
 * Filter + sort the bookmarks visible in the current sidebar scope.
 *
 * Scope rules (faithful mirror of session pins, worktree-aware):
 * - All-projects (global) view: only `all`-scoped bookmarks.
 * - Workspace view: `workspace`/`all` bookmarks whose owning project is in the
 *   workspace (workspaceContainsProject maps worktrees up to their main repo).
 * - Single-project view: every bookmark whose owning project is in
 *   `projectScopeIds` (the viewed project's main repo + its worktrees), so a
 *   worktree session's bookmark surfaces in the main-repo project view.
 *
 * @param {Object}   args
 * @param {Object}   args.bookmarks          - store.artifactBookmarks map { id: bookmark }
 * @param {Object}   args.workspaces         - workspaces store (workspaceContainsProject)
 * @param {string}   args.effectiveProjectId - current scope id
 * @param {string?}  args.activeWorkspaceId  - current workspace id (workspace scope)
 * @param {string[]} args.projectScopeIds    - data.getProjectScopeIds(effectiveProjectId)
 * @returns {Array} bookmarks, flat, sorted by recency (updated_at desc)
 */
export function computeArtifactBookmarkList({ bookmarks, workspaces, effectiveProjectId, activeWorkspaceId, projectScopeIds }) {
    const list = Object.values(bookmarks).filter(b => {
        if (effectiveProjectId === ALL_PROJECTS_ID) return b.scope === 'all'
        if (isWorkspaceProjectId(effectiveProjectId)) {
            if (b.scope === 'project') return false
            return workspaces.workspaceContainsProject(activeWorkspaceId, b.project_id)
        }
        return projectScopeIds.includes(b.project_id) // single project (worktree-aware)
    })
    return list.sort((a, b) =>
        a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0) // recency desc
}
