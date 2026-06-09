// Canonical derivation of a project's root directories.
//
// Every Files / Git / file-path-resolution site derives its roots from these
// helpers so the set stays consistent. When a project is a git worktree
// (``worktree_of`` set), the main repository's directories are appended as
// additional roots so the worktree's tabs can reach the parent checkout.
//
// Pure module: it takes the data store as an argument and imports no
// store / router, so it never participates in an import cycle.

/**
 * The main-repo project a worktree links to, or null.
 * Single source of truth for "who is my parent".
 */
export function getWorktreeParent(project, store) {
    const parentId = project?.worktree_of
    if (!parentId) return null
    return store.getProject(parentId) || null
}

/**
 * Human-readable label for a Files-tab root, from its set of roles.
 */
function buildFileRootLabel(roles) {
    const has = (r) => roles.has(r)
    if (has('parent_dir') && has('parent_git')) return 'Main repo directory (git root)'
    if (has('parent_dir')) return 'Main repo directory'
    if (has('parent_git')) return 'Main repo git root'
    if (has('project_dir') && has('git_root')) return 'Project directory (git root)'
    if (has('project_dir')) return 'Project directory'
    if (has('git_root')) return 'Git root'
    if (has('cwd')) return 'Working directory'
    return 'Directory'
}

/**
 * Ordered root descriptors for the Files tab and file-path resolution.
 *
 * Order: the worktree's own roots first (git_directory | cwd | project, exactly
 * as before), then — when a worktree parent is present — the main repo's git
 * root and directory. Same-path candidates merge into a single descriptor;
 * later duplicates are dropped (so the worktree keeps priority over the parent).
 *
 * @returns {Array<{ key: string, label: string, path: string, roles: string[] }>}
 */
export function deriveFileRoots({
    gitDirectory = null,
    cwd = null,
    projectDirectory = null,
    projectGitRoot = null,
    parentDirectory = null,
    parentGitRoot = null,
} = {}) {
    const sessionGit = gitDirectory
    const pathRoles = new Map()  // path → { key, roles: Set }

    function register(path, role, key) {
        if (!path) return
        if (pathRoles.has(path)) {
            pathRoles.get(path).roles.add(role)
        } else {
            pathRoles.set(path, { key, roles: new Set([role]) })
        }
    }

    if (sessionGit) {
        register(sessionGit, 'git_root', 'git-root')
    }
    register(cwd, 'cwd', 'session')
    register(projectDirectory, 'project_dir', 'project')
    if (!sessionGit) {
        register(projectGitRoot, 'git_root', 'git-root')
    }
    // Worktree parent (main repo) roots, appended after the worktree's own.
    register(parentGitRoot, 'parent_git', 'parent-git')
    register(parentDirectory, 'parent_dir', 'parent-dir')

    const base = sessionGit
        ? [sessionGit, cwd, projectDirectory]
        : [projectDirectory, cwd, projectGitRoot]
    const order = [...base, parentGitRoot, parentDirectory]

    const roots = []
    const seen = new Set()
    for (const path of order) {
        if (!path || seen.has(path)) continue
        seen.add(path)
        const info = pathRoles.get(path)
        roots.push({
            key: info.key,
            label: buildFileRootLabel(info.roles),
            path,
            roles: [...info.roles],
        })
    }
    return roots
}

/**
 * Ordered git-root descriptors for the Git tab. The worktree's own git roots
 * first (session git, project git), then the main repo's git root when present.
 *
 * @returns {Array<{ key: string, label: string, path: string }>}
 */
export function deriveGitRoots({ gitDirectory = null, projectGitRoot = null, parentGitRoot = null } = {}) {
    const sessionGit = gitDirectory
    const projectGit = projectGitRoot
    const parentGit = parentGitRoot

    const roots = []
    const seen = new Set()
    function push(key, label, path) {
        if (!path || seen.has(path)) return
        seen.add(path)
        roots.push({ key, label, path })
    }

    if (sessionGit && projectGit && sessionGit === projectGit) {
        // Worktree session git and project git resolve to the same path.
        push('session', 'Git root', sessionGit)
    } else {
        push('session', 'Session git root', sessionGit)
        push('project', 'Project git root', projectGit)
    }
    push('parent', 'Main repo git root', parentGit)
    return roots
}

/**
 * ``deriveFileRoots`` from store objects, resolving the worktree parent.
 */
export function fileRootsFromStore(project, session, store) {
    const parent = getWorktreeParent(project, store)
    return deriveFileRoots({
        gitDirectory: session?.git_directory,
        cwd: session?.cwd,
        projectDirectory: project?.directory,
        projectGitRoot: project?.git_root,
        parentDirectory: parent?.directory,
        parentGitRoot: parent?.git_root,
    })
}

/**
 * ``deriveGitRoots`` from store objects, resolving the worktree parent.
 */
export function gitRootsFromStore(project, session, store) {
    const parent = getWorktreeParent(project, store)
    return deriveGitRoots({
        gitDirectory: session?.git_directory,
        projectGitRoot: project?.git_root,
        parentGitRoot: parent?.git_root,
    })
}
