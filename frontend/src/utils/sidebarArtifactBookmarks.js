// Scope resolution for the artifact bookmark list, mirroring the session-pin
// rules in computeSidebarSessionBlocks. ALL_PROJECTS_ID is exported by the data
// store; isWorkspaceProjectId by workspaceIds (same modules sidebarSessions.js
// imports from).
import { ALL_PROJECTS_ID } from '../stores/data'
import { isWorkspaceProjectId } from './workspaceIds'
import { getArtifactBookmarkVisibilitySource } from './sessionArtifactBookmarks'

/**
 * Filter + sort the bookmarks visible in the current sidebar scope.
 *
 * Scope rules (faithful mirror of session pins, worktree-aware):
 * - All-projects (global) view: only `all`-scoped bookmarks.
 * - Workspace view: `workspace`/`all` bookmarks whose owning project is in the
 *   workspace (workspaceContainsProject maps worktrees up to their main repo).
 * - Project/worktree view: the same project-context visibility as a session's
 *   Artifacts tab (local context, shared workspaces, then global bookmarks).
 *
 * @param {Object}   args
 * @param {Object}   args.bookmarks          - store.artifactBookmarks map { id: bookmark }
 * @param {Object}   args.workspaces         - workspaces store (workspaceContainsProject)
 * @param {string}   args.effectiveProjectId - current scope id
 * @param {string?}  args.activeWorkspaceId  - current workspace id (workspace scope)
 * @param {string}   args.mainProjectId      - parent repo id for a worktree, otherwise the project id
 * @param {boolean}  [args.showAll]          - ignore scope and return every bookmark
 * @returns {Array} bookmarks, flat, sorted by recency (updated_at desc)
 */
export function computeArtifactBookmarkList({
    bookmarks,
    workspaces,
    effectiveProjectId,
    activeWorkspaceId,
    mainProjectId,
    showAll = false,
}) {
    const all = Object.values(bookmarks)
    const localProjectIds = new Set([effectiveProjectId, mainProjectId].filter(Boolean))
    const sharedWorkspaceIds = workspaces.workspaces
        .filter(ws => !ws.archived && workspaces.workspaceContainsProject(ws.id, mainProjectId))
        .map(ws => ws.id)
    const list = showAll ? all : all.filter(b => {
        if (effectiveProjectId === ALL_PROJECTS_ID) return b.scope === 'all'
        if (isWorkspaceProjectId(effectiveProjectId)) {
            if (b.scope === 'project') return false
            return workspaces.workspaceContainsProject(activeWorkspaceId, b.project_id)
        }
        return !!getArtifactBookmarkVisibilitySource({
            bookmark: b,
            localProjectIds,
            sharedWorkspaceIds,
            workspaceContainsProject: workspaces.workspaceContainsProject,
        })
    })
    return list.sort((a, b) =>
        a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0) // recency desc
}
