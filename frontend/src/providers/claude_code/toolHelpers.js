/**
 * Tool-rendering helpers for Claude Code sessions.
 *
 * Consumed by:
 *   - components/session/detail/items/ToolUseContent.vue (rich card)
 *   - components/session/detail/items/WorkingAssistantMessage.vue (status line)
 *
 * Encapsulates everything that's specific to Claude Code's tool catalogue
 * (Edit/Write/Read/Bash/Grep/Glob/Web{*}/Skill/Task/MCP/...) — naming,
 * summary formatting, which Vue component renders an input or a result, the
 * JsonHumanView overrides, the file-change stats source, and the backend-
 * patch extractor. The generic shell never branches on tool name itself.
 */

import { PROVIDER, AGENT_TOOL_NAMES } from '../../constants'
import { getTodoDescription, isValidTodos } from '../../utils/todoList'
import { BaseToolHelpers } from '../baseHelpers'
import { formatRelativePath, fileIconFor } from '../utils/path'

import EditContent from '../../components/session/detail/items/claude_code/EditContent.vue'
import WriteContent from '../../components/session/detail/items/claude_code/WriteContent.vue'
import TodoContent from '../../components/session/detail/items/claude_code/TodoContent.vue'
import ReadResultContent from '../../components/session/detail/items/claude_code/ReadResultContent.vue'
import BashResultContent from '../../components/session/detail/items/claude_code/BashResultContent.vue'
import WebContentResult from '../../components/session/detail/items/claude_code/WebContentResult.vue'

import DescriptionSummary from '../../components/session/detail/items/summary/DescriptionSummary.vue'
import SkillSummary from '../../components/session/detail/items/claude_code/summary/SkillSummary.vue'
import GrepSummary from '../../components/session/detail/items/summary/GrepSummary.vue'
import GlobSummary from '../../components/session/detail/items/claude_code/summary/GlobSummary.vue'
import WebFetchSummary from '../../components/session/detail/items/claude_code/summary/WebFetchSummary.vue'
import WebSearchSummary from '../../components/session/detail/items/claude_code/summary/WebSearchSummary.vue'
import ToolSearchSummary from '../../components/session/detail/items/claude_code/summary/ToolSearchSummary.vue'
import TodoSummary from '../../components/session/detail/items/claude_code/summary/TodoSummary.vue'

const FILE_PATH_TOOLS = new Set(['Edit', 'Write', 'Read'])
const FILE_CHANGE_TOOLS = new Set(['Edit', 'Write'])
const DIRECT_CONTENT_TOOLS = new Set(['Bash', 'WebFetch', 'WebSearch'])
const TASK_SUBAGENT_LABELS = {
    explore: 'exploring',
    plan: 'planning',
    bash: 'bashing',
}

const INPUT_OVERRIDES = {
    Bash: { command: { valueType: 'string-code', language: 'bash' } },
}

const CAT_N_LINE_RE = /^(\s*\d+)[→\t](.*)$/

// ─── Helpers private to this module ─────────────────────────────────────

function capitalize(str) {
    return str.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

function getDisplayName(name, input) {
    if (AGENT_TOOL_NAMES.has(name)) {
        const sat = input?.subagent_type
        if (!sat || sat === 'general-purpose') return null
        const colonIdx = sat.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(sat.slice(colonIdx + 1)),
                namespace: capitalize(sat.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(sat), namespace: null }
    }
    if (name === 'Skill' && input?.skill) {
        const skill = input.skill
        const colonIdx = skill.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(skill.slice(colonIdx + 1)),
                namespace: capitalize(skill.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(skill), namespace: null }
    }
    return null
}

function buildGrepInline(pattern, fileType, path) {
    const parts = []
    if (pattern) parts.push(pattern)
    if (fileType) parts.push(`in ${fileType} files`)
    if (path) parts.push(`in ${path}`)
    return parts.length ? parts.join(' ') : null
}

function parseCatNContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null
    return true
}

function getDirectContent(name, result) {
    if (!DIRECT_CONTENT_TOOLS.has(name)) return null
    const content = typeof result === 'string' ? result : result?.content
    if (typeof content !== 'string' || !content) return null
    return content
}

// ─── ClaudeCodeToolHelpers ──────────────────────────────────────────────

export class ClaudeCodeToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    // ─── Summary ─────────────────────────────────────────────────────────

    computeToolSummary(name, input, baseDir) {
        const safeInput = input || {}
        const displayName = getDisplayName(name, safeInput)

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            const description = formatRelativePath(safeInput.file_path, baseDir)
            return { displayName, inline: description }
        }

        if (name === 'Skill' && displayName) {
            return { displayName, inline: displayName.name }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                const path = rawPath ? formatRelativePath(rawPath, baseDir) : null
                return {
                    displayName,
                    inline: buildGrepInline(pattern, fileType, path),
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return { displayName, inline: safeInput.pattern }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return { displayName, inline: safeInput.url }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { displayName, inline: null }
        }

        const description = safeInput.description || null
        return { displayName, inline: description }
    }

    getVerb(name, input) {
        if (!name) return null
        if (AGENT_TOOL_NAMES.has(name)) {
            const subtype = input?.subagent_type?.toLowerCase()
            if (subtype && TASK_SUBAGENT_LABELS[subtype]) {
                return TASK_SUBAGENT_LABELS[subtype]
            }
            return 'agenting'
        }
        if (name.startsWith('mcp__')) {
            const parts = name.split('__')
            const server = parts[1] || 'mcp'
            return `mcping (${server})`
        }
        const lower = name.toLowerCase()
        return lower.replace(/[aeiou]+$/, '') + 'ing'
    }

    getHeaderLabel(name) {
        if (name === 'TodoWrite') return 'Todo'
        return null
    }

    // ─── Input / Result rendering ───────────────────────────────────────

    getInputRendering(name, input, ctx) {
        const safeInput = input || {}
        const editProps = {
            input: safeInput,
            backendPatch: ctx?.backendPatch ?? null,
            backendPatchLoading: ctx?.backendPatchLoading ?? false,
            originalFile: ctx?.originalFile ?? null,
            isSubagent: !!ctx?.isSubagent,
        }
        if (name === 'Edit' && 'old_string' in safeInput && 'new_string' in safeInput) {
            return { component: EditContent, props: editProps }
        }
        if (name === 'Write' && 'content' in safeInput) {
            return { component: WriteContent, props: editProps }
        }
        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { component: TodoContent, props: { todos: safeInput.todos } }
        }
        return null
    }

    getResultRendering(name, result, input /*, ctx */) {
        if (name === 'Read') {
            const content = typeof result === 'string' ? result : result?.content
            if (parseCatNContent(content)) {
                return { component: ReadResultContent, props: { result, input } }
            }
            return null
        }
        if (name === 'Bash' && getDirectContent(name, result) != null) {
            return { component: BashResultContent, props: { result } }
        }
        if ((name === 'WebFetch' || name === 'WebSearch') && getDirectContent(name, result) != null) {
            return { component: WebContentResult, props: { result } }
        }
        return null
    }

    getSummaryRendering(name, input, baseDir /*, ctx */) {
        const safeInput = input || {}

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            return {
                component: DescriptionSummary,
                props: {
                    description: formatRelativePath(safeInput.file_path, baseDir),
                    fileIconSrc: fileIconFor(safeInput.file_path),
                },
            }
        }

        if (name === 'Skill') {
            const dn = getDisplayName(name, safeInput)
            if (dn) {
                return {
                    component: SkillSummary,
                    props: { name: dn.name, namespace: dn.namespace },
                }
            }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern,
                        fileType,
                        path: rawPath ? formatRelativePath(rawPath, baseDir) : null,
                        pathIconSrc: rawPath ? fileIconFor(rawPath) : null,
                    },
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return {
                component: GlobSummary,
                props: { pattern: safeInput.pattern },
            }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return {
                component: WebFetchSummary,
                props: { url: safeInput.url },
            }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return {
                component: WebSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return {
                component: ToolSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return {
                component: TodoSummary,
                props: { parts: getTodoDescription(safeInput.todos) },
            }
        }

        // Fallback: any tool whose input carries a `description` field.
        if (safeInput.description) {
            return {
                component: DescriptionSummary,
                props: { description: safeInput.description, fileIconSrc: null },
            }
        }

        return null
    }

    getInputOverrides(name) {
        return INPUT_OVERRIDES[name] ?? {}
    }

    // ─── Input value extraction ──────────────────────────────────────────

    getFilePath(name, input) {
        if (!FILE_PATH_TOOLS.has(name)) return null
        return input?.file_path ?? null
    }

    getOpenInFilesTarget(name, input, ctx) {
        const filePath = this.getFilePath(name, input)
        if (!filePath) return null
        // Edit/Write: scroll to the first modified line from the backend patch.
        // Read: scroll to the offset line carried by the tool input.
        const lineHint = ctx?.firstModifiedLine ?? input?.offset ?? null
        return { filePath, lineHint }
    }

    getDisplayInputObject(_name, input) {
        if (!input || Object.keys(input).length === 0) return null
        // ``description`` is already rendered in the summary header — strip it
        // from the JsonHumanView fallback so it isn't shown twice.
        const { description: _description, ...rest } = input
        return Object.keys(rest).length > 0 ? rest : null
    }

    getExpectedResultCount(_name, input) {
        // Claude Code's Bash tool emits two tool_result events when launched
        // with ``run_in_background``: one for the start, one for the final
        // output. Every other tool emits a single result.
        return input?.run_in_background ? 2 : 1
    }

    // ─── Capability flags ────────────────────────────────────────────────

    isFileChangeTool(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    isAgentTool(name) {
        return AGENT_TOOL_NAMES.has(name)
    }

    shouldAutoOpenLive(name /*, input */) {
        return name === 'Edit' || name === 'Write'
    }

    showsResultOnError(name) {
        // Bash errors only carry "Exit code N" — the full output is in the result.
        return name === 'Bash'
    }

    showsResultOnUnknownError(name) {
        // Edit/Write benefit from the diagnostic detail in the Result section;
        // TodoWrite never shows the Result (its specialized renderer is enough).
        return name === 'Edit' || name === 'Write'
    }

    errorIsMarkdown(name) {
        // ExitPlanMode's error text is the user-rejected plan, formatted as Markdown.
        return name === 'ExitPlanMode'
    }

    // ─── File-change stats ───────────────────────────────────────────────

    computeFileChangeStats(name, input, toolState, isSubagent) {
        if (!FILE_CHANGE_TOOLS.has(name)) return null

        // Subagent: stats come from the input directly (no backend patch).
        if (isSubagent) {
            if (name === 'Edit' && input?.old_string != null && input?.new_string != null) {
                return {
                    lines_removed: input.old_string.split('\n').length,
                    lines_added: input.new_string.split('\n').length,
                }
            }
            if (name === 'Write' && input?.content != null) {
                return { lines_added: input.content.split('\n').length }
            }
            return null
        }

        // Main session: stats come from the backend (ToolResultLink.extra JSON).
        if (!toolState?.extra) return null
        try {
            return JSON.parse(toolState.extra)
        } catch {
            return null
        }
    }

    // ─── Backend-patch fetcher ───────────────────────────────────────────

    needsBackendPatchFetch(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    extractBackendPatchData(parsedToolResult) {
        const toolUseResult = parsedToolResult?.toolUseResult
        if (!toolUseResult) return null
        const patch = toolUseResult.structuredPatch
        const originalFile = toolUseResult.originalFile
        const out = {}
        if (Array.isArray(patch) && patch.length > 0) out.patch = patch
        if (typeof originalFile === 'string') out.originalFile = originalFile
        return Object.keys(out).length > 0 ? out : null
    }
}

export const claudeCodeToolHelpers = new ClaudeCodeToolHelpers()
