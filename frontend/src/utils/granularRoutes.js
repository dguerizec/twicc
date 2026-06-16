export function encodePath(path) {
    if (!path) return undefined
    return path.split('/').map(encodeURIComponent).join('|')
}

export function decodePath(encoded) {
    if (!encoded) return undefined
    try {
        return encoded.split('|').map(decodeURIComponent).join('/')
    } catch {
        return null
    }
}

export function pickDefined(obj) {
    return Object.fromEntries(
        Object.entries(obj).filter(([, value]) => value != null)
    )
}

function firstParamValue(value) {
    return Array.isArray(value) ? value[0] : value
}

export function parseRouteString(value) {
    const raw = firstParamValue(value)
    return typeof raw === 'string' && raw !== '' ? raw : undefined
}

export function parseRouteTermIndex(value) {
    const raw = firstParamValue(value)
    if (raw == null || raw === '') return undefined
    const parsed = Number.parseInt(raw, 10)
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : null
}

export function buildTabRouteName({ isAllProjectsMode = false, isSessionRoute = false, tab }) {
    if (isSessionRoute) {
        return isAllProjectsMode ? `projects-session-${tab}` : `session-${tab}`
    }
    return isAllProjectsMode ? `projects-${tab}` : `project-${tab}`
}

export function buildSessionBaseRouteName(isAllProjectsMode = false) {
    return isAllProjectsMode ? 'projects-session' : 'session'
}

export function buildSubagentRouteName(isAllProjectsMode = false) {
    return isAllProjectsMode ? 'projects-session-subagent' : 'session-subagent'
}

export function buildProjectBaseRouteName(isAllProjectsMode = false) {
    return isAllProjectsMode ? 'projects-all' : 'project'
}

export function buildTerminalRouteParams({ termIndex }) {
    return pickDefined({
        termIndex: termIndex == null ? undefined : String(termIndex),
    })
}

export function buildFilesRouteParams({ rootKey, filePath }) {
    return pickDefined({
        rootKey,
        filePath: filePath ? encodePath(filePath) : undefined,
    })
}

export function buildGitRouteParams({ rootKey, commitRef, filePath }) {
    return pickDefined({
        rootKey,
        commitRef,
        filePath: filePath ? encodePath(filePath) : undefined,
    })
}

export function clearTabRouteParams(tab, params = {}) {
    // Artifacts shares the files route shape (rootKey + filePath); its root is
    // fixed ('artifacts') but it reuses the same FilesPanel routing plumbing.
    if (tab === 'files' || tab === 'artifacts') {
        return {
            rootKey: null,
            filePath: null,
            ...params,
        }
    }

    if (tab === 'git') {
        return {
            rootKey: null,
            commitRef: null,
            filePath: null,
            ...params,
        }
    }

    if (tab === 'terminal') {
        return {
            termIndex: null,
            ...params,
        }
    }

    return params
}
