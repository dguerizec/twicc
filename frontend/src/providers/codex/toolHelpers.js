/**
 * Tool-rendering helpers for Codex sessions.
 *
 * Codex's ``exec_command`` tool is a single shell-running entry point —
 * the model packs ``cat foo``, ``rg PATTERN``, ``ls`` and arbitrary
 * scripts under the same name. The Rust runtime used to surface a
 * structured ``parsed_cmd`` (Read / ListFiles / Search / Unknown) on
 * ``event_msg.exec_command_end``, but the CLI no longer persists that
 * event. We rely on our local ``parseCommand`` classifier
 * (see ``parseCommand.js``) for the header label and summary line, and
 * we reconstruct the rich aggregated output by walking the chain of
 * ``function_call_output`` rows that the backend rebinds to a single
 * ``tool_use_id`` (the parent ``exec_command``'s ``call_id`` — the
 * write_stdin polling outputs hang off it via
 * ``CodexSessionCompute.remap_tool_result_id``).
 *
 * The shell still uses ``getExpectedResultCount`` for tools that have
 * a persisted ``*_end`` event (apply_patch, MCP, web_search,
 * image_generation) and switches to a status-based ``isToolRunning``
 * for the exec_command family.
 */

import { PROVIDER } from '../../constants'
import { BaseToolHelpers } from '../baseHelpers'
import { formatRelativePath, fileIconFor, resolveAbsolutePath } from '../utils/path'
import { parseCommand } from './parseCommand'
import { parseApplyPatchEnvelope } from './parsePatch'
import { getParsedContent } from '../../utils/parsedContent'
import { getTodoDescription } from '../../utils/todoList'

import DescriptionSummary from '../../components/session/detail/items/summary/DescriptionSummary.vue'
import GrepSummary from '../../components/session/detail/items/summary/GrepSummary.vue'
import MultiFileSummary from '../../components/session/detail/items/summary/MultiFileSummary.vue'
import TodoSummary from '../../components/session/detail/items/summary/TodoSummary.vue'
import ExecResultContent from '../../components/session/detail/items/codex/ExecResultContent.vue'
import ReadResultContent from '../../components/session/detail/items/codex/ReadResultContent.vue'
import ApplyPatchContent from '../../components/session/detail/items/codex/ApplyPatchContent.vue'
import TodoContent from '../../components/session/detail/items/TodoContent.vue'

// ``function_call`` tools that produce / consume a unified-exec
// process. Two facts at once for these tools:
//   - their ``function_call_output`` rows chain together (the parent
//     ``exec_command``'s own row plus one row per ``write_stdin``
//     poll), all rebound to the same ``tool_use_id`` server-side, with
//     status driven by :meth:`isToolRunning` reading
//     ``extra.is_terminated``;
//   - their input carries a parseable shell command we can feed to the
//     local ``parseCommand`` parser to derive the header label
//     (Read / List files / Grep / Exec).
// Two input shapes are in play: ``exec_command`` (unified_exec) ships
// the raw script as ``input.cmd`` (string); the others ship a
// ``Vec<String>`` argv as ``input.command``, typically
// ``["bash", "-lc", "..."]``.
const FUNCTION_CALL_EXEC_TOOLS = new Set([
    'exec_command',
    'shell',
    'shell_command',
    'local_shell',
    'container.exec',
])

// ``function_call`` tools with a persisted ``event_msg.*_end`` event
// paired by ``call_id``. Source of truth: ``rollout/src/policy.rs`` in
// the Codex repo (Limited persistence). ``apply_patch``'s JSON variant
// is here; its Freeform variant lands as a ``custom_tool_call`` and is
// handled inline in :meth:`getExpectedResultCount`.
const FUNCTION_CALL_TOOLS_WITH_END_EVENT = new Set([
    'apply_patch',
])

// ``event_msg.*_end`` sub-types we still consume — mirrors the backend
// whitelist :data:`twicc.providers.codex.compute._PERSISTED_END_EVENT_TYPES`.
const PERSISTED_END_EVENT_TYPES = new Set([
    'patch_apply_end',
    'mcp_tool_call_end',
    'web_search_end',
    'image_generation_end',
])

// MCP tools are dispatched as ``custom_tool_call`` and their names are
// always namespaced with this prefix (see ``ToolName::namespaced`` in
// the Codex source). We branch on the prefix because the actual name
// is dynamic (``mcp__<server>__<tool>``).
const MCP_TOOL_NAME_PREFIX = 'mcp__'

// Per-tool ``JsonHumanView`` overrides used when the Result/Input
// fallback rendering kicks in. Mirrors Claude Code's pattern: a tiny
// table keyed by tool name → ``{ key: { valueType, language } }``.
// Add new entries here as more Codex tools get tool-cards.
const INPUT_OVERRIDES = {
    exec_command: {
        // ``cmd`` is the raw shell script the model wants Codex to run.
        // Render it as a fenced bash block so it's syntax-coloured by
        // Shiki, the same way Claude Code's Bash ``command`` is shown.
        cmd: { valueType: 'string-code', language: 'bash' },
    },
}

// Per-tool whitelist of input keys to drop from the JSON fallback
// (kept out of the tool body but not from the raw JSON view, which is
// always reachable through the ``</>`` toggle). Stripped keys are
// usually internal knobs the user doesn't need to read on every call.
// Schema source for ``exec_command``: ``ExecCommandArgs`` in
// ``codex-rs/core/src/tools/handlers/unified_exec.rs``.
const STRIPPED_INPUT_KEYS_BY_TOOL = {
    exec_command: new Set([
        'workdir',
        // Internal: how long the runtime waits before yielding partial
        // output back to the model (default 10s). Implementation knob,
        // not interesting to readers.
        'yield_time_ms',
        // Internal: per-call truncation budget for the aggregated
        // output. Useful only when comparing it with the actual output
        // length, which we don't surface here either.
        'max_output_tokens',
        // Always present (defaults to false) but rarely meaningful.
        // We accept that the rare ``tty: true`` case is hidden — that
        // can come back as a dedicated badge later if needed.
        'tty',
        // Permission machinery — technical objects (default profile,
        // additional grants, command-prefix patterns) that don't
        // describe what the call *does*.
        'sandbox_permissions',
        'additional_permissions',
        'prefix_rule',
    ]),
}

/**
 * Pull the command payload from a tool_use ``input`` according to the
 * tool's input shape. Returns ``null`` when the tool isn't one we
 * parse, or when the expected field is missing.
 */
function extractCommandPayload(name, input) {
    if (!input) return null
    if (name === 'exec_command') {
        return typeof input.cmd === 'string' ? input.cmd : null
    }
    return Array.isArray(input.command) ? input.command : null
}

/**
 * Locate the ``event_msg.*_end`` line that pairs with this tool_use
 * by ``call_id`` and return its parsed payload. Used for tools that
 * still get a structured End event in the rollout (apply_patch, MCP,
 * web_search, image_generation). Returns ``null`` when the result line
 * hasn't arrived yet (live session) or isn't loaded in the store.
 *
 * Direct lookup via the ``toolStates`` index (keyed by tool_use_id).
 * The API exposes every persisted ``ToolResultLink`` line number via
 * ``toolState.toolResultLineNums`` so we just iterate that list and
 * return the first ``event_msg`` whose ``call_id`` and sub-type match
 * the whitelist :data:`PERSISTED_END_EVENT_TYPES`.
 */
function findCodexEndEventPayload(toolId, options) {
    if (!toolId) return null
    const toolState = options?.getToolState?.(toolId)
    const lineNums = toolState?.toolResultLineNums
    if (!Array.isArray(lineNums) || lineNums.length === 0) return null
    const getSessionItem = options?.getSessionItem
    if (typeof getSessionItem !== 'function') return null
    for (const ln of lineNums) {
        if (!Number.isInteger(ln) || ln < 1) continue
        const item = getSessionItem(ln)
        if (!item) continue
        const parsed = getParsedContent(item)
        if (!parsed || parsed.type !== 'event_msg') continue
        const payload = parsed.payload
        if (!payload || typeof payload !== 'object') continue
        if (!PERSISTED_END_EVENT_TYPES.has(payload.type)) continue
        if (payload.call_id !== toolId) continue
        return payload
    }
    return null
}

// Match the formatted trailer Codex emits on every exec_command /
// write_stdin ``function_call_output`` (see
// ``codex-rs/core/src/tools/context.rs``). Mirrors the backend's
// :func:`twicc.providers.codex.compute.parse_exec_command_status`
// — kept inline here so the front isn't tied to backend regex churn.
const EXEC_COMMAND_STATUS_RE = /^Process (?:running with session ID (?<run>-?\d+)|exited with code (?<exit>-?\d+))$/m

// The body of a Codex tool output starts with this marker. Anything
// before it (Chunk ID / Wall time / Process … / Original token count)
// is structured trailer; the actual stdout/stderr lives after.
const OUTPUT_BODY_PREFIX_RE = /^Output:\n?/m

/**
 * Walk the chain of ``function_call_output`` rows attached to this
 * tool_use_id and concatenate every body in order. Used by the
 * exec_command family — the backend rebinds every ``write_stdin``
 * polling output to the parent ``exec_command``'s tool_use_id, so
 * ``toolState.toolResultLineNums`` lists every chunk in source order
 * and we just stitch them back together.
 *
 * Returns ``null`` when nothing usable is in the store yet, otherwise
 * an object ready to feed :class:`ExecResultContent` /
 * :class:`ReadResultContent`:
 *   - ``aggregatedOutput``: concatenated bodies (string).
 *   - ``isTerminated``: ``true`` once any chunk reported a
 *     ``Process exited`` line.
 *   - ``exitCode``: parsed code on the closing chunk (or ``null``).
 */
function aggregateExecCommandOutput(toolId, options) {
    if (!toolId) return null
    const toolState = options?.getToolState?.(toolId)
    const lineNums = toolState?.toolResultLineNums
    if (!Array.isArray(lineNums) || lineNums.length === 0) return null
    const getSessionItem = options?.getSessionItem
    if (typeof getSessionItem !== 'function') return null
    const bodies = []
    let isTerminated = false
    let exitCode = null
    for (const ln of lineNums) {
        if (!Number.isInteger(ln) || ln < 1) continue
        const item = getSessionItem(ln)
        if (!item) continue
        const parsed = getParsedContent(item)
        if (!parsed || parsed.type !== 'response_item') continue
        const payload = parsed.payload
        if (!payload || typeof payload !== 'object') continue
        if (payload.type !== 'function_call_output' && payload.type !== 'custom_tool_call_output') continue
        const output = typeof payload.output === 'string' ? payload.output : ''
        if (!output) continue
        // Status trailer (running/exited).
        const statusMatch = EXEC_COMMAND_STATUS_RE.exec(output)
        if (statusMatch?.groups?.exit !== undefined) {
            isTerminated = true
            const code = parseInt(statusMatch.groups.exit, 10)
            if (Number.isFinite(code)) exitCode = code
        }
        // Body lives after ``Output:\n`` (which is always emitted, even
        // when the body is empty). Anything without that marker is
        // either malformed or not a unified-exec output — skip.
        const bodyMatch = OUTPUT_BODY_PREFIX_RE.exec(output)
        if (!bodyMatch) continue
        const body = output.slice(bodyMatch.index + bodyMatch[0].length)
        if (body) bodies.push(body)
    }
    if (bodies.length === 0 && !isTerminated) return null
    return {
        aggregatedOutput: bodies.join(''),
        isTerminated,
        exitCode,
    }
}

/**
 * Make ``path`` (as parsed out of a shell command) absolute against
 * the call's ``workdir`` (when present), then relative to the session
 * base dir. Required because ``parseCommand`` returns paths verbatim
 * from the command — typically already relative to the call's working
 * directory — so a literal ``formatRelativePath`` against the session
 * cwd misses the case where the model ran the tool from a sub-folder
 * (e.g. ``cd frontend && rg foo src/`` → path is ``src/`` relative to
 * ``frontend/``, not the session root). Absolute paths short-circuit
 * the ``workdir`` step. Falls back to ``baseDir`` when ``workdir``
 * isn't supplied so the legacy behaviour is preserved.
 */
function relPathFromWorkdir(path, input, baseDir) {
    if (!path) return path
    const workdir = (input && typeof input.workdir === 'string' && input.workdir) || baseDir
    const absPath = resolveAbsolutePath(path, workdir)
    return formatRelativePath(absPath, baseDir)
}

/**
 * Locate the matching ``event_msg.patch_apply_end`` payload for an
 * ``apply_patch`` call. Same shape as ``findCodexEndEventPayload``
 * but filtered on the patch-specific subtype (defensive — the
 * whitelist already excludes the unrelated end events but a tool_use
 * could in theory share a call_id with a different shape).
 */
function findPatchApplyEndPayload(toolId, options) {
    const payload = findCodexEndEventPayload(toolId, options)
    if (payload && payload.type === 'patch_apply_end') return payload
    return null
}

/**
 * Resolve the file paths an ``apply_patch`` call touches, with
 * supersedence:
 *   1. ``patch_apply_end.changes`` keys when loaded — the canonical,
 *      absolute paths the runtime actually applied to.
 *   2. Local v4a parser otherwise — what the model declared in its
 *      ``input``, available immediately on the tool_use line.
 * Returns ``[]`` when neither source yields anything.
 */
function resolveApplyPatchPaths(input, options) {
    const payload = findPatchApplyEndPayload(options?.toolId, options)
    if (payload && payload.changes && typeof payload.changes === 'object') {
        const keys = Object.keys(payload.changes)
        if (keys.length > 0) return keys
    }
    const parsed = parseApplyPatchEnvelope(typeof input === 'string' ? input : input?.input)
    return parsed.map((f) => f.path).filter(Boolean)
}

/**
 * Resolve the ``ParsedCommand[]`` to feed ``mergeStages`` /
 * ``pickPrimary``. Codex used to surface a tree-sitter-bash
 * ``parsed_cmd`` on the ``exec_command_end`` event but no longer
 * persists it (TUI flipped to ``persist_extended_history=false`` on
 * 2026-04-30), so the local ``parseCommand`` estimate is now the
 * canonical source. Returns ``null`` when the command shape isn't one
 * we know how to extract.
 */
function resolveParsedCommand(name, input) {
    const payload = extractCommandPayload(name, input)
    if (payload === null) return null
    return parseCommand(payload)
}

const HEADER_LABELS_BY_VARIANT = {
    read: 'Read',
    list_files: 'List files',
    // ``Grep`` reads better than ``Search`` for shell users: it
    // mirrors what the underlying tools (rg / grep / git grep) are.
    search: 'Grep',
}

// Priority used by ``pickPrimary``: more specific variants win.
// ``search`` carries a query (the most informative item), then ``read``
// (concrete file), then ``list_files`` (broader scope), then
// ``unknown`` (raw command). Ties are broken by "last wins" so the
// rightmost stage of an equal-priority sequence is the primary one.
const VARIANT_PRIORITY = { search: 3, read: 2, list_files: 1, unknown: 0 }

/**
 * Post-process a ``ParsedCommand[]`` to merge known combos into a
 * single richer stage. Applied between the parser (ours or Codex's
 * ``parsed_cmd``) and ``pickPrimary``, so the same rules drive both
 * sources.
 *
 * Current rule:
 *   - ``list_files`` / ``read`` immediately followed by ``search``
 *     without its own ``path`` → keep the upstream **type** (the
 *     operation is still a listing or a read; the trailing ``search``
 *     just filters its output) and graft the search's ``query`` onto
 *     the upstream entry. Captures pipelines like
 *     ``rg --files . | rg PATTERN`` (still a list of files, narrowed
 *     by name) or ``cat foo | grep bar`` (still a read, narrowed by
 *     content). The summary renderer treats a ``list_files`` / ``read``
 *     with a ``query`` field as a Grep-style display so the query is
 *     surfaced; the header label stays ``List files`` / ``Read``.
 *
 * The ``query`` field on ``list_files`` / ``read`` is a TwiCC-only
 * extension over the Codex ``ParsedCommand`` schema — fine, since
 * downstream code only consumes the merged structure.
 */
function mergeStages(parsed) {
    if (!Array.isArray(parsed) || parsed.length < 2) return parsed
    const out = []
    let i = 0
    while (i < parsed.length) {
        const cur = parsed[i]
        const next = parsed[i + 1]
        const isSourceForSearch = cur && (cur.type === 'list_files' || cur.type === 'read') && cur.path
        const isPathlessSearch = next && next.type === 'search' && !next.path
        if (isSourceForSearch && isPathlessSearch) {
            out.push({ ...cur, query: next.query })
            i += 2
        } else {
            out.push(cur)
            i += 1
        }
    }
    return out
}

/**
 * Pick the "most informative" stage from a parsed command sequence,
 * using ``VARIANT_PRIORITY``. Returns ``null`` for an empty input.
 * When everything is ``unknown`` we still return the last entry so
 * callers can render its raw ``cmd`` text.
 */
function pickPrimary(parsed) {
    if (!parsed || parsed.length === 0) return null
    let best = null
    let bestScore = -1
    for (const p of parsed) {
        const score = VARIANT_PRIORITY[p.type] ?? 0
        if (score >= bestScore) {  // ``>=`` → ties resolved by last wins
            best = p
            bestScore = score
        }
    }
    return best
}

/**
 * First line of ``cmd`` for the ``unknown`` summary variant. When the
 * source has more than one line we explicitly append ``…`` so the
 * truncation is visible even if the (possibly short) first line fits
 * on the row. When the source is a single line, we hand it back as-is
 * and rely on the surrounding CSS ``text-overflow: ellipsis`` to add
 * the ``…`` only when the text actually overflows the row width.
 */
function firstLine(cmd) {
    if (typeof cmd !== 'string') return ''
    const idx = cmd.indexOf('\n')
    return idx >= 0 ? cmd.slice(0, idx) + '…' : cmd
}

// ─── update_plan ────────────────────────────────────────────────────────
//
// Codex's ``update_plan`` is the moral equivalent of Claude Code's
// ``TodoWrite``: a list of plan items, each with a free-form text and
// one of the same three statuses (pending / in_progress / completed).
// We map it to the same renderers (``TodoContent`` / ``TodoSummary``)
// by normalising every entry to ``{ content, status }`` — Claude Code
// also has an ``activeForm`` field that Codex doesn't, so we leave it
// undefined and let the shared helpers fall back to ``content``.
// Source spec: ``codex-rs/core/src/tools/handlers/plan_spec.rs``.

function isValidPlan(plan) {
    if (!Array.isArray(plan) || plan.length === 0) return false
    return plan.every(p =>
        p != null && typeof p === 'object' &&
        typeof p.step === 'string' &&
        typeof p.status === 'string',
    )
}

function planToTodos(plan) {
    return plan.map(p => ({ content: p.step, status: p.status }))
}

export class CodexToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CODEX

    getExpectedResultCount(name, _input, options) {
        const wrapperType = options?.wrapperType
        if (wrapperType === 'function_call') {
            // shell family chains a variable number of
            // ``function_call_output`` rows; the spinner is driven by
            // :meth:`isToolRunning` reading ``extra.is_terminated``
            // instead of a fixed count.
            if (FUNCTION_CALL_EXEC_TOOLS.has(name)) return 1
            return FUNCTION_CALL_TOOLS_WITH_END_EVENT.has(name) ? 2 : 1
        }
        if (wrapperType === 'custom_tool_call') {
            // apply_patch (Freeform variant) and any MCP tool both have
            // a persisted ``*_end`` / ``mcp_tool_call_end`` paired by
            // call_id. The third known custom_tool_call shape — the
            // code_mode ``exec`` tool — does *not*, so it falls through.
            if (name === 'apply_patch') return 2
            if (typeof name === 'string' && name.startsWith(MCP_TOOL_NAME_PREFIX)) return 2
            return 1
        }
        // local_shell_call, web_search_call, image_generation_call: the
        // ``*_end`` event is the only result (no separate ``*_call_output``
        // payload). Single ToolResultLink, single result.
        return 1
    }

    getRequiredResultCountForDisplay(name, input, options) {
        // Shell tools render progressively from a single chunk (the
        // ``aggregateExecCommandOutput`` helper concatenates whatever
        // is in the store), so 1 is enough; everything else mirrors
        // ``getExpectedResultCount``.
        if (FUNCTION_CALL_EXEC_TOOLS.has(name)) return 1
        return this.getExpectedResultCount(name, input, options)
    }

    isToolRunning(name, input, options) {
        // Shell tools: status comes from the chain's last chunk via the
        // ``is_terminated`` flag the backend set on
        // ``ToolResultLink.extra``. ``Max``-aggregated across links so
        // any closing chunk flips the whole tool to "done".
        if (FUNCTION_CALL_EXEC_TOOLS.has(name)) {
            const extra = options?.toolState?.extra
            if (!extra) return true
            // ``extra`` is the JSON string set by
            // :meth:`compute_link_extra` — parse defensively so the
            // shell never crashes on unexpected shapes (live race,
            // malformed payload).
            try {
                const parsed = typeof extra === 'string' ? JSON.parse(extra) : extra
                return !parsed?.is_terminated
            } catch {
                return true
            }
        }
        return super.isToolRunning(name, input, options)
    }

    shouldAggregateExecOutput(name) {
        return FUNCTION_CALL_EXEC_TOOLS.has(name)
    }

    getAggregatedExecOutput(toolId, options) {
        return aggregateExecCommandOutput(toolId, options)
    }

    getHeaderLabel(name, input, options) {
        // ``apply_patch`` is the model's verb, not the user-facing
        // operation. Mirror Claude Code's ``Edit`` header so users see
        // the same word regardless of provider.
        if (name === 'apply_patch') return 'Edit'
        // Same idea for ``update_plan`` → ``Todo``: the tool plays the
        // role of Claude Code's ``TodoWrite``, so users see the same
        // header word across providers.
        if (name === 'update_plan') return 'Todo'
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null
        return HEADER_LABELS_BY_VARIANT[primary.type] ?? 'Shell'
    }

    getSummaryRendering(name, input, baseDir, options) {
        if (name === 'update_plan' && isValidPlan(input?.plan)) {
            return {
                component: TodoSummary,
                props: { parts: getTodoDescription(planToTodos(input.plan)) },
            }
        }
        if (name === 'apply_patch') {
            const paths = resolveApplyPatchPaths(input, options)
            if (paths.length === 0) return null
            if (paths.length === 1) {
                return {
                    component: DescriptionSummary,
                    props: {
                        description: formatRelativePath(paths[0], baseDir),
                        fileIconSrc: fileIconFor(paths[0]),
                    },
                }
            }
            // Multi-file: each file gets its own icon + relative path,
            // separated by commas. No truncation — the summary line is
            // free to wrap if needed (like ``WorkingAssistantMessage``
            // does for long status lines).
            return {
                component: MultiFileSummary,
                props: {
                    files: paths.map((p) => ({
                        path: formatRelativePath(p, baseDir),
                        fileIconSrc: fileIconFor(p),
                    })),
                },
            }
        }
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null

        if (primary.type === 'read') {
            const relPath = relPathFromWorkdir(primary.path, input, baseDir)
            // ``query`` is set when ``mergeStages`` paired this read
            // with a downstream search filter (``cat foo | grep bar``).
            // Show the query alongside the path via GrepSummary.
            if (primary.query) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern: primary.query,
                        fileType: null,
                        path: relPath,
                        pathIconSrc: fileIconFor(primary.path),
                    },
                }
            }
            return {
                component: DescriptionSummary,
                props: {
                    description: relPath,
                    fileIconSrc: fileIconFor(primary.path),
                },
            }
        }

        if (primary.type === 'list_files') {
            // No icon for list_files: directories don't have a file-icon
            // mapping and the generic ``default-file`` glyph would be
            // misleading (we'd be claiming the path is a file).
            const relPath = relPathFromWorkdir(primary.path, input, baseDir)
            if (!relPath) return null
            // ``query`` is set when ``mergeStages`` paired this listing
            // with a downstream search filter on file names
            // (``rg --files . | rg PATTERN``). Surface the query
            // through the GrepSummary layout.
            if (primary.query) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern: primary.query,
                        fileType: null,
                        path: relPath,
                        pathIconSrc: null,
                    },
                }
            }
            return {
                component: DescriptionSummary,
                props: { description: relPath, fileIconSrc: null },
            }
        }

        if (primary.type === 'search') {
            const relPath = primary.path ? relPathFromWorkdir(primary.path, input, baseDir) : null
            return {
                component: GrepSummary,
                props: {
                    pattern: primary.query ?? null,
                    fileType: null,
                    path: relPath,
                    pathIconSrc: primary.path ? fileIconFor(primary.path) : null,
                },
            }
        }

        // Unknown / fallback: show the first line of the raw command,
        // forced to a single ellipsis-truncated row (the script may be
        // arbitrarily long and shouldn't wrap into the next line).
        const inline = firstLine(primary.cmd)
        if (!inline) return null
        return {
            component: DescriptionSummary,
            props: { description: inline, fileIconSrc: null, truncate: true },
        }
    }

    getInputRendering(name, input, ctx) {
        if (name === 'apply_patch') {
            const raw = typeof input === 'string' ? input : input?.input
            if (typeof raw !== 'string' || !raw) return null
            return {
                component: ApplyPatchContent,
                props: {
                    input: raw,
                    sessionId: ctx?.sessionId ?? '',
                    toolId: ctx?.toolId ?? '',
                    isSubagent: !!ctx?.isSubagent,
                },
            }
        }
        if (name === 'update_plan' && isValidPlan(input?.plan)) {
            return {
                component: TodoContent,
                props: {
                    todos: planToTodos(input.plan),
                    explanation: typeof input.explanation === 'string' && input.explanation
                        ? input.explanation
                        : null,
                },
            }
        }
        return null
    }

    getResultRendering(name, _result, input, options) {
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        // The shell precomputed the chain aggregate when
        // :meth:`shouldAggregateExecOutput` returned ``true``; reach
        // for it through ``options.aggregatedExecOutput``. ``_result``
        // (the raw row from ``displayResult``) is unused here — for
        // long-running shells it's just one chunk among many, and for
        // synchronous one-shots the aggregator already collapsed it.
        const aggregated = options?.aggregatedExecOutput
        if (!aggregated || typeof aggregated.aggregatedOutput !== 'string') return null
        if (!aggregated.aggregatedOutput && !aggregated.isTerminated) return null

        // ``read``-classified calls get the same treatment as Claude
        // Code's Read tool: try to extract ``cat -n`` / ``nl -ba``
        // line numbers, surface them as a "Lines X–Y" header, and
        // colour the code by the file's extension. The
        // tree-sitter-bash ``parsed_cmd`` Codex used to surface on
        // ``exec_command_end`` is gone, so the local ``parseCommand``
        // estimate is now the only source.
        const stages = parseCommand(extractCommandPayload(name, input))
        const primary = pickPrimary(mergeStages(stages || []))
        if (primary?.type === 'read' && primary.path) {
            return {
                component: ReadResultContent,
                props: {
                    aggregatedOutput: aggregated.aggregatedOutput,
                    path: primary.path,
                },
            }
        }

        // ``ExecResultContent`` originally received the raw
        // ``exec_command_end`` payload as ``result``; we hand it a
        // synthetic object with the same ``aggregated_output`` shape so
        // the component itself doesn't need to know the source changed.
        return {
            component: ExecResultContent,
            props: { result: { aggregated_output: aggregated.aggregatedOutput } },
        }
    }

    showsResultOnError(name) {
        // Same rationale as Claude Code's Bash: the error callout only
        // surfaces a short label (``Exit code N`` / ``Patch failed``),
        // so the rich event payload (stdout, stderr, applied changes)
        // is still useful and should stay visible.
        if (name === 'apply_patch') return true
        // ``update_plan`` errors out when called in Plan mode — the
        // attempted plan is still informative, keep the body shown.
        if (name === 'update_plan') return true
        return FUNCTION_CALL_EXEC_TOOLS.has(name)
    }

    getInputOverrides(name) {
        return INPUT_OVERRIDES[name] ?? {}
    }

    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        // ``apply_patch`` only has the raw v4a envelope as input; the
        // ``ApplyPatchContent`` renderer takes over the full body, so
        // there's nothing useful left for the JSON fallback.
        if (name === 'apply_patch') return null
        // ``update_plan`` is fully rendered by ``TodoContent`` (plan +
        // explanation), so the JSON fallback would only duplicate it.
        if (name === 'update_plan') return null
        const stripped = STRIPPED_INPUT_KEYS_BY_TOOL[name]
        if (!stripped || stripped.size === 0) return input
        const out = {}
        for (const k of Object.keys(input)) {
            if (!stripped.has(k)) out[k] = input[k]
        }
        return Object.keys(out).length > 0 ? out : null
    }

    isFileChangeTool(name) {
        return name === 'apply_patch'
    }

    getFilePath(name, input) {
        if (name !== 'apply_patch') return null
        const parsed = parseApplyPatchEnvelope(typeof input === 'string' ? input : input?.input)
        // Single-file: surface the path so the shell shows a
        // ``View in Files tab`` button next to the summary, like Edit
        // / Write. Multi-file: bail — a single button can only point
        // to one path, so each per-file header inside the body adds
        // its own button instead.
        if (parsed.length !== 1) return null
        return parsed[0]?.path ?? null
    }

    shouldAutoOpenLive(name) {
        // Same UX as Claude Code's Edit / Write: when the user has
        // ``showDiffs`` enabled, an apply_patch tool_use that arrives
        // live is auto-expanded so the diff is visible without a click.
        return name === 'apply_patch'
    }

    computeFileChangeStats(name, _input, toolState, _isSubagent) {
        if (name !== 'apply_patch') return null
        if (!toolState?.extra) return null
        try {
            return JSON.parse(toolState.extra)
        } catch {
            return null
        }
    }
}

export const codexToolHelpers = new CodexToolHelpers()
