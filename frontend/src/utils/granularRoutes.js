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

// Terminal route segment. Own tabs are a plain index ("0", "1", …). Attached
// parent-scope terminals use a scoped token that maps to the pool key
// `${contextKey}#${index}`:
//   global    → "all:<idx>"            ↔  "global#<idx>"
//   workspace → "w:<wsId>:<idx>"       ↔  "w:<wsId>#<idx>"
//   project   → "p:<projectId>:<idx>"  ↔  "p:<projectId>#<idx>"
// parseRouteTermIndex returns: a number (own), the pool key string (attached),
// null (invalid), or undefined (absent).
export function parseRouteTermIndex(value) {
    const raw = firstParamValue(value)
    if (raw == null || raw === '') return undefined
    if (/^\d+$/.test(raw)) {
        const parsed = Number.parseInt(raw, 10)
        return parsed >= 0 ? parsed : null
    }
    // Attached token "<scope>:<idx>" (scope may itself contain ':', e.g. "p:foo")
    const m = raw.match(/^(.+):(\d+)$/)
    if (m) {
        const scope = m[1]
        const contextKey = scope === 'all'
            ? 'global'
            : (scope.startsWith('w:') || scope.startsWith('p:')) ? scope : null
        if (contextKey) return `${contextKey}#${m[2]}`
    }
    return null
}

// Inverse of the attached-token mapping: a pool key `${contextKey}#${index}`
// (or a plain own index) → the URL term segment.
export function terminalRouteToken(termIndex) {
    if (typeof termIndex === 'number') return String(termIndex)
    const key = String(termIndex)
    const hash = key.lastIndexOf('#')
    if (hash === -1) return key
    const contextKey = key.slice(0, hash)
    const idx = key.slice(hash + 1)
    const scope = contextKey === 'global' ? 'all' : contextKey
    return `${scope}:${idx}`
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
        termIndex: termIndex == null ? undefined : terminalRouteToken(termIndex),
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
