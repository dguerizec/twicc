// frontend/src/utils/sessionRoute.js
//
// Single source of truth for "where does navigating to a session go", given the
// current route context. Shared by SessionListItem (its sidebar link) and the
// session switcher (its commit target) so a keyboard-driven jump lands exactly
// where clicking the sidebar row would.

/**
 * Build the router location for a session, honouring the current route context.
 * In all-projects mode (`projects-*` routes) the session keeps its own project
 * id; otherwise it stays under the route's current project. The active
 * `workspace` query param is preserved.
 *
 * @param {{ id: string, project_id: string }} session
 * @param {import('vue-router').RouteLocationNormalized} route - the current route
 * @returns {import('vue-router').RouteLocationRaw}
 */
export function sessionRouteLocation(session, route) {
    const isAllProjects = route.name?.startsWith('projects-')
    const location = isAllProjects
        ? { name: 'projects-session', params: { projectId: session.project_id, sessionId: session.id } }
        : { name: 'session', params: { projectId: route.params.projectId, sessionId: session.id } }
    if (route.query.workspace) {
        location.query = { workspace: route.query.workspace }
    }
    return location
}
