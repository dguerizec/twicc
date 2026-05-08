/**
 * Tool-rendering helpers for Codex sessions.
 *
 * Codex's ``exec_command`` tool is a single shell-running entry point —
 * the model packs ``cat foo``, ``rg PATTERN``, ``ls`` and arbitrary
 * scripts under the same name. The Rust runtime classifies each call
 * into a ``ParsedCommand`` (Read / ListFiles / Search / Unknown) and
 * surfaces the result via the ``event_msg.exec_command_end`` event.
 * We mimic that classification on the front (see ``parseCommand.js``)
 * so the tool_use card already has a meaningful header label and
 * summary line *before* the result arrives — and even on sessions
 * captured by older Codex CLIs that don't persist
 * ``exec_command_end``. When the real ``parsed_cmd`` arrives via the
 * tool_result, we'll happily prefer it over our local estimate (TBD).
 *
 * Other helpers (``getExpectedResultCount``) drive the running spinner
 * — see the comment block at the top of the file's previous revision.
 */

import { PROVIDER } from '../../constants'
import { BaseToolHelpers } from '../baseHelpers'
import { formatRelativePath, fileIconFor } from '../utils/path'
import { parseCommand } from './parseCommand'
import { getParsedContent } from '../../utils/parsedContent'

import DescriptionSummary from '../../components/session/detail/items/summary/DescriptionSummary.vue'
import GrepSummary from '../../components/session/detail/items/summary/GrepSummary.vue'
import ExecResultContent from '../../components/session/detail/items/codex/ExecResultContent.vue'

// ``function_call`` tools whose call is followed by a persisted
// ``event_msg.<X>_end`` event paired by ``call_id``. Source of truth:
// ``rollout/src/policy.rs`` in the Codex repo (Limited persistence).
// ``apply_patch`` is listed here for its JSON variant; its Freeform
// variant is dispatched as ``custom_tool_call`` and handled below.
const FUNCTION_CALL_TOOLS_WITH_END_EVENT = new Set([
    'exec_command',
    'shell',
    'shell_command',
    'local_shell',
    'container.exec',
    'apply_patch',
])

// MCP tools are dispatched as ``custom_tool_call`` and their names are
// always namespaced with this prefix (see ``ToolName::namespaced`` in
// the Codex source). We branch on the prefix because the actual name
// is dynamic (``mcp__<server>__<tool>``).
const MCP_TOOL_NAME_PREFIX = 'mcp__'

// Tools whose summary header benefits from running ``parseCommand``
// on the input. Two input shapes are in play in the Codex catalogue:
//   - ``exec_command`` (unified_exec): ``input.cmd`` is a raw shell
//     script (string).
//   - ``shell`` / ``local_shell`` / ``shell_command`` / ``container.exec``:
//     ``input.command`` is a ``Vec<String>`` argv, typically
//     ``["bash", "-lc", "..."]``.
const PARSED_COMMAND_TOOLS = new Set([
    'exec_command', 'shell', 'shell_command', 'local_shell', 'container.exec',
])

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
 * by ``call_id`` and return its parsed payload. Returns ``null`` when
 * the result line hasn't arrived yet (live session, or session
 * captured by a Codex CLI < 0.121.0 that didn't persist
 * ``exec_command_end``) or isn't loaded in the store.
 *
 * Linear scan over the session's items. In practice sessions hold a
 * few hundred items at most and Vue memoises this through the
 * ``getSummaryRendering`` computed, so the cost is negligible.
 */
function findCodexResultPayload(toolId, sessionItems) {
    if (!toolId || !Array.isArray(sessionItems) || sessionItems.length === 0) return null
    for (const item of sessionItems) {
        const parsed = getParsedContent(item)
        if (!parsed || parsed.type !== 'event_msg') continue
        const payload = parsed.payload
        if (!payload || typeof payload !== 'object') continue
        if (payload.call_id !== toolId) continue
        return payload
    }
    return null
}

/**
 * Resolve the ``ParsedCommand[]`` to feed ``mergeStages`` /
 * ``pickPrimary``. Strict preference order:
 *   1. The official ``parsed_cmd`` carried by the matching
 *      ``event_msg.*_end`` event when loaded in the store. Codex's
 *      runtime parser is strictly richer than ours (tree-sitter-bash,
 *      full bin catalogue), so we always defer to it once available.
 *   2. Our local ``parseCommand`` estimate, used while the result
 *      hasn't arrived yet or for sessions where ``exec_command_end``
 *      isn't persisted (< 0.121.0).
 * The fallback ensures the summary header is meaningful immediately
 * — before any tool_result — and the supersedence is transparent
 * (Vue recomputes when the result line lands in the store).
 */
function resolveParsedCommand(name, input, options) {
    const codexResult = findCodexResultPayload(options?.toolId, options?.sessionItems)
    const officialParsedCmd = codexResult?.parsed_cmd
    if (Array.isArray(officialParsedCmd) && officialParsedCmd.length > 0) {
        return officialParsedCmd
    }
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

export class CodexToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CODEX

    getExpectedResultCount(name, _input, options) {
        const wrapperType = options?.wrapperType
        if (wrapperType === 'function_call') {
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
        // For Codex tools that emit two tool_results, the second row
        // (``event_msg.*_end``) is the one carrying the structured
        // outcome we want to render — the first
        // (``function_call_output``) is a partial LLM-facing snippet.
        // Wait for both before showing anything; while we're waiting,
        // the shell renders "Result not yet available …" + spinner +
        // polling, exactly like a regular foreground tool with no
        // result yet.
        return this.getExpectedResultCount(name, input, options)
    }

    getHeaderLabel(name, input, options) {
        if (!PARSED_COMMAND_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input, options)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null
        return HEADER_LABELS_BY_VARIANT[primary.type] ?? 'Exec'
    }

    getSummaryRendering(name, input, baseDir, options) {
        if (!PARSED_COMMAND_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input, options)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null

        if (primary.type === 'read') {
            const relPath = formatRelativePath(primary.path, baseDir)
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
            const relPath = formatRelativePath(primary.path, baseDir)
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
            const relPath = primary.path ? formatRelativePath(primary.path, baseDir) : null
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

    getResultRendering(name, result /*, input, ctx */) {
        if (!PARSED_COMMAND_TOOLS.has(name)) return null
        // ``result`` is whatever ``displayResult`` handed us — a single
        // payload object when there's only one row, or an array when
        // there are several. ``CodexHelpers.get_tool_results`` returns
        // each row's ``payload`` raw, so we identify the
        // ``event_msg.exec_command_end`` entry by its
        // ``aggregated_output`` field (the ``function_call_output``
        // payload only carries ``output``).
        const candidates = Array.isArray(result) ? result : [result]
        const execEnd = candidates.find(
            (r) => r && typeof r.aggregated_output === 'string',
        )
        if (!execEnd) return null
        return { component: ExecResultContent, props: { result: execEnd } }
    }

    showsResultOnError(name) {
        // Same rationale as Claude Code's Bash: the error callout only
        // surfaces "Exit code N", so the actual stdout/stderr of the
        // failed command is still useful and should stay visible.
        return PARSED_COMMAND_TOOLS.has(name)
    }

    getInputOverrides(name) {
        return INPUT_OVERRIDES[name] ?? {}
    }

    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        const stripped = STRIPPED_INPUT_KEYS_BY_TOOL[name]
        if (!stripped || stripped.size === 0) return input
        const out = {}
        for (const k of Object.keys(input)) {
            if (!stripped.has(k)) out[k] = input[k]
        }
        return Object.keys(out).length > 0 ? out : null
    }
}

export const codexToolHelpers = new CodexToolHelpers()
