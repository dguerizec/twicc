"""
Compute pipeline for Codex sessions.

Each Codex JSONL line is wrapped in ``{timestamp, type, payload}``; this
pass turns the wrapper into a TwiCC :class:`~twicc.core.enums.ItemKind`
and, for tool calls, lets the inherited base orchestration build the
``ToolResultLink`` rows that pair a call with its result.

Classification rules (any change MUST bump CODEX_COMPUTE_VERSION):

- ``event_msg.user_message`` → ``USER_MESSAGE``
- ``event_msg.agent_message`` → ``ASSISTANT_MESSAGE``
- ``event_msg.*`` whose sub-type is in :data:`_PERSISTED_END_EVENT_TYPES`
  (``patch_apply_end``, ``mcp_tool_call_end``) → kind stays ``None``;
  routed to ``DEBUG_ONLY`` via :meth:`is_tool_result_item`. Pairs with the
  matching ``function_call`` / ``custom_tool_call`` by ``call_id``. These
  events carry the structured outcome of the tool (``changes`` map,
  ``CallToolResult``, …) and coexist as a second :class:`ToolResultLink`
  row alongside the LLM-facing ``function_call_output`` for the same
  tool_use_id. ``web_search_end`` is intentionally absent — see the
  ``response_item.web_search_call`` rule below.
- ``event_msg.image_generation_end`` → ``IMAGE`` (-> ``ALWAYS``). Codex
  emits this line right after generating an image; the payload carries
  the ``revised_prompt`` (the actual prompt the image generator received
  after the model rewrote the user's request), the base64-encoded PNG
  ``result``, and the on-disk ``saved_path`` (typically under
  ``~/.codex/generated_images/<session>/<call_id>.png``). The matching
  ``response_item.image_generation_call`` duplicates ``revised_prompt``
  and ``result`` (no ``saved_path``), so we ignore it — it falls through
  to ``SYSTEM`` / ``DEBUG_ONLY`` like any other unhandled response_item.
  No tool_use → tool_result pairing: the event alone carries everything
  the frontend needs to render the image, the prompt and the path inline,
  and the image is already fully baked when the line lands (no streaming,
  no spinner). The matching frontend component is
  ``items/codex/ImageGeneration.vue``.
- ``event_msg.exec_command_end`` is intentionally **not** in the list:
  Codex CLI no longer persists it (TUI sets
  ``persist_extended_history=false`` since 2026-04-30) so we
  reconstruct the same surface from the chain of
  ``function_call_output`` lines instead — the original ``exec_command``
  output plus every ``write_stdin`` polling output sharing the same
  unified-exec process id (called ``session_id`` by Codex,
  ``exec_command_id`` here).
- ``response_item.function_call`` / ``custom_tool_call`` /
  ``local_shell_call`` / ``web_search_call`` → ``TOOL_USE`` (->
  ``COLLAPSIBLE``), except ``function_call name=write_stdin`` which is
  bucketed as ``SYSTEM`` (no tool card). Its ``function_call_output``
  is rebound to the parent ``exec_command``'s ``call_id`` via
  :meth:`CodexSessionCompute.remap_tool_result_id`. ``local_shell_call``
  doesn't carry a ``name`` field — its tool name is the sub_type itself
  (``"local_shell_call"``), supplied via
  :data:`_NATIVE_TOOL_NAME_BY_SUB_TYPE` in
  :meth:`extract_tool_use_entries` / :meth:`analyze_content`. Its result
  is a single ``function_call_output`` paired by ``call_id`` (no chained
  ``write_stdin`` polls, and unlike ``exec_command`` it does **not**
  emit a Codex unified-exec status trailer — instead its ``output`` is
  a JSON-encoded string ``{"output":"<body>","metadata":{"exit_code":N,
  "duration_seconds":N.N}}`` produced by
  ``format_exec_output_for_model_structured`` in
  ``codex-rs/core/src/tools/mod.rs``). The exit-code surface for
  :class:`ToolResultLink.is_error` therefore goes through
  :func:`_structured_exec_output_error` (JSON decode + ``metadata.exit_code``
  test), and :meth:`compute_link_extra` flags the matching result row as
  terminated on arrival so the frontend stops the spinner.
  ``web_search_call`` is a **resultless** tool (see
  :data:`_RESULTLESS_TOOL_SUB_TYPES`): no ``call_id`` is serialised
  on the call, so the matching ``event_msg.web_search_end`` can't be
  paired from the JSONL and is intentionally ignored (kept out of
  :data:`_PERSISTED_END_EVENT_TYPES`). The tool_use card stands alone
  — no ``ToolResultLink``, no spinner; ``analyze_content`` emits a
  visible-but-unpaired :class:`ContentAnalysis` for it.
- ``response_item.{function_call_output, custom_tool_call_output}`` →
  kind stays ``None`` (-> ``DEBUG_ONLY``). Pairs as a tool_result.
  For exec_command long-running shells the chain accumulates one row
  per polling write_stdin; for everything else there's a single row
  (plus the matching event_msg.*_end when applicable).
- top-level ``compacted`` → ``COMPACT_SUMMARY`` (lands at ``ALWAYS``).
  Codex CLI writes this line on auto-compaction; the payload carries
  a ``replacement_history`` of the messages that were summarised plus
  an encrypted summary in
  ``replacement_history[-1].encrypted_content``. We pick this wrapper
  over the redundant ``event_msg.context_compacted`` event because
  the encrypted field gives us a future-proof landing spot if Codex
  ever ships a readable summary. The matching
  ``event_msg.context_compacted`` line stays bucketed as ``SYSTEM``.
- everything else (``session_meta``, ``turn_context``, other
  ``response_item`` subtypes, other ``event_msg`` subtypes without
  ``call_id`` including ``event_msg.context_compacted``) → ``SYSTEM``
  (lands at ``DEBUG_ONLY``).

The ``call_id`` carried by every line above is the pairing key,
stored as-is in ``ToolResultLink.tool_use_id`` (analogous to Claude's
``tool_use_id``).

Token counts and costs are computed by
:meth:`CodexSessionCompute.compute_item_cost_and_usage` from
``event_msg.token_count`` events: ``last_token_usage`` is mapped to
the cross-provider :class:`TokenUsage` via :func:`to_token_usage` and
priced with the model carried by the running ``turn_context``;
``info.total_token_usage.total_tokens`` acts as a monotonic clock to
filter non-billable events (bootstrap snapshot, inter-turn
re-emission, compaction-zero) — see the method docstring for details.

Custom titles, session-start detection and subagent linkage remain
out of scope at this stage. Runtime environment fields are partially
wired: ``cwd`` and ``cwd_git_branch`` come from the opening
``session_meta`` line, ``cwd`` plus ``model`` come from each
``turn_context`` line, and ``context_max`` comes from the
``event_msg.task_started.model_context_window`` emitted at every turn
start (the base orchestrator's "last non-null wins" rule means a
mid-session ``cd`` / model swap / window change is reflected on
``Session.cwd`` / ``Session.model`` / ``Session.context_max``).
``slug`` is unused (Codex doesn't expose one). File-change stats are
wired for ``apply_patch`` (aggregated ``+`` / ``-`` from the
``patch_apply_end.changes`` map). Other hooks return empty / no-op
values so the inherited base machinery (group state, batch compute,
title extraction) still runs cleanly.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar, NamedTuple

import orjson

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import SessionItem
from twicc.pricing import calculate_line_context_usage
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    ToolResultInfo,
    ToolUseEntry,
    parse_timestamp_to_datetime,
)

from .pricing import extract_model_info, to_token_usage


# Keys at the wrapper level. Every Codex JSONL line is
# ``{"timestamp": ..., "type": ..., "payload": {...}}`` so we always
# go through ``payload`` to reach Codex-specific fields.
_TYPE_EVENT_MSG = "event_msg"
_TYPE_RESPONSE_ITEM = "response_item"
# ``session_meta`` is the opening line of a Codex JSONL (one per
# session) — carries the initial cwd + native git branch. ``turn_context``
# is emitted on every turn — carries the current cwd and model. Both
# feed :meth:`CodexSessionCompute.extract_runtime_fields`.
_TYPE_SESSION_META = "session_meta"
_TYPE_TURN_CONTEXT = "turn_context"
# ``compacted`` is the top-level wrapper Codex CLI writes when it auto-
# compacts the rolling context. The payload carries a ``replacement_history``
# of the messages that were summarised plus a trailing
# ``{"type":"compaction","encrypted_content":"..."}`` entry — the
# summary itself is encrypted, so we can't surface a body, only mark
# the item as a ``COMPACT_SUMMARY`` so the UI shows the standard
# divider. The matching ``event_msg.context_compacted`` event is
# redundant for our purposes and stays bucketed as SYSTEM.
_TYPE_COMPACTED = "compacted"
_PAYLOAD_USER_MESSAGE = "user_message"
_PAYLOAD_AGENT_MESSAGE = "agent_message"
# Codex emits ``event_msg.image_generation_end`` once an image generation
# call has produced its file. The payload carries the base64 PNG, the
# revised prompt and the on-disk path — see the header docstring's
# ``event_msg.image_generation_end → IMAGE`` rule for the full contract.
# Referenced by :meth:`compute_item_kind` and :meth:`analyze_content`.
_PAYLOAD_IMAGE_GENERATION_END = "image_generation_end"
# ``event_msg.token_count`` is the only Codex line that carries usage
# counters (``info.last_token_usage`` for the last LLM call,
# ``info.total_token_usage`` for the cumulative session totals). Read
# by :meth:`CodexSessionCompute.compute_item_cost_and_usage`.
_PAYLOAD_TOKEN_COUNT = "token_count"
# ``event_msg.task_started`` is emitted at the start of every turn and
# carries the active model's context window in ``model_context_window``.
# That value is **not** the nominal input window of the model: Codex
# CLI publishes its internal compaction threshold instead — 95% of the
# nominal input window, the rest left as headroom for the auto-compact
# logic. For ``gpt-5.x`` the nominal input window is 272K (the
# advertised 400K total = 272K input + 128K output reserved), so the
# JSONL reports 272_000 × 0.95 = 258_400 on every ``task_started``.
# We divide back by the factor below to recover the nominal window
# the user expects to see in the UI (and to keep the ring meaningful
# across the auto-compact step). Read by
# :meth:`CodexSessionCompute.extract_runtime_fields` to populate
# ``Session.context_max`` for sessions imported from JSONL.
_PAYLOAD_TASK_STARTED = "task_started"
# Compaction headroom Codex CLI reserves on top of the model's nominal
# input window, expressed as the ratio of "published" to "nominal".
# Used to recover the nominal window from
# ``task_started.model_context_window``. If Codex changes the
# headroom in a future release this constant will need adjusting (or
# the math replaced by an explicit per-model lookup).
_TASK_STARTED_WINDOW_HEADROOM_FACTOR = 0.95

# response_item payload sub-types that represent a tool call. Each is its
# own JSONL line (mono-block), unlike Claude where tool_uses live inside a
# message.content array. ``function_call`` is the standard OpenAI form;
# ``custom_tool_call`` is the freeform variant used for tools whose input
# isn't JSON (apply_patch ships its patch as raw Lark-grammar text);
# ``local_shell_call`` is the native shell tool exposed directly by the
# Responses API — it doesn't carry a ``name`` field (the sub_type IS the
# tool name) and ships its argv via ``payload.action`` instead of a JSON
# ``arguments`` string; ``web_search_call`` is the native web-search tool
# (also nameless, also payload.action-based) — see
# :data:`_NATIVE_TOOL_NAME_BY_SUB_TYPE` and :data:`_RESULTLESS_TOOL_SUB_TYPES`.
_TOOL_CALL_PAYLOAD_TYPES = frozenset({
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "web_search_call",
})

# Sub-types whose canonical tool name is the sub_type itself — used as a
# fallback in :meth:`extract_tool_use_entries` / :meth:`analyze_content`
# when the payload doesn't carry a ``name`` field. The frontend reads
# this name verbatim (no rewriting) so the value here is what the tool
# card / helpers (label, summary, INPUT_OVERRIDES, …) key off.
_NATIVE_TOOL_NAME_BY_SUB_TYPE = {
    "local_shell_call": "local_shell_call",
    "web_search_call": "web_search_call",
}

# Sub-types of :data:`_TOOL_CALL_PAYLOAD_TYPES` that never produce a
# paired ``function_call_output`` (or equivalent) — the tool_use card
# stands alone with no result to wait for, so the frontend's spinner
# stays off from the start.
#
# Today: ``web_search_call``. Codex emits a ``response_item.web_search_call``
# alongside an ``event_msg.web_search_end``, but the call doesn't carry
# a ``call_id`` or any serialised id (``id`` is ``skip_serializing``
# on ``WebSearchCall``), so the event can't be paired with the call from
# the JSONL — and the call has nothing else to wait for. ``analyze_content``
# therefore short-circuits to a visible-but-unpaired ContentAnalysis for
# these sub-types (no ``call_id`` requirement, empty ``tool_use_entries``).
# Frontend mirrors via the ``RESULTLESS_TOOLS`` set in toolHelpers.js.
_RESULTLESS_TOOL_SUB_TYPES = frozenset({"web_search_call"})

# Shell-family tools that share the shell-card rendering path on the
# frontend. Membership here drives :meth:`compute_link_extra`'s
# ``extra.is_terminated`` logic — see :data:`_EXEC_COMMAND_TOOLS` for
# the chained subset.
#
# The rule of thumb: any new shell-like tool we want to surface should
# go in this set, and is treated as **atomic** (single
# ``function_call_output`` per call, terminated on arrival) by default.
# Only ``exec_command`` and ``write_stdin`` (the unified-exec pair
# already in :data:`_EXEC_COMMAND_TOOLS`) can chain multiple result
# rows for the same call_id — Codex CLI only spawns ``write_stdin``
# polls against an ``exec_command`` parent, never against ``shell`` /
# ``shell_command`` / ``local_shell_call`` whose output is always a
# complete one-shot payload (cf. their handlers — they don't expose a
# unified-exec process id to poll).
#
_SHELL_FAMILY_TOOLS = frozenset({
    "exec_command",
    "write_stdin",
    "shell",
    "shell_command",
    "local_shell_call",
    # ``container.exec`` is a legacy alias of ``shell`` — same wire shape
    # (function_call, ``ShellToolCallParams`` arguments) and same output
    # path (``run_exec_like(freeform=false)`` ->
    # ``format_exec_output_for_model_structured``). Hosted by
    # ``ContainerExecHandler`` (``codex-rs/core/src/tools/handlers/shell/container_exec.rs``).
    "container.exec",
})

# Function-call ``name`` values whose tool_use is bucketed as SYSTEM (no
# tool card rendered) because the relevant exchange is captured elsewhere.
# ``write_stdin`` belongs to a previously-spawned ``exec_command`` session;
# its ``function_call_output`` is rebound to the parent exec_command's
# ``call_id`` via :meth:`CodexSessionCompute.remap_tool_result_id` so the
# polled chunks all land on the same ``ToolResultLink`` chain.
#
# NOTE: this list governs UI rendering only (``compute_item_kind`` returns
# ``SYSTEM`` for these). The pairing path (``extract_tool_use_entries``,
# ``analyze_content``) still records the call_id in ``tool_use_map`` so
# the remap hook can resolve a write_stdin output to its parent
# exec_command — without an entry in the map, there's nothing to remap.
_NON_TOOL_FUNCTION_NAMES = frozenset({"write_stdin"})

# Tool-result payload sub-types from ``response_item`` lines (the
# LLM-facing string returned to the model). Paired with the calls above
# by ``call_id`` and routed to DEBUG_ONLY via :meth:`is_tool_result_item`.
_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})

# function_call ``name`` values that produce / consume a unified-exec
# process. ``exec_command`` spawns the process; ``write_stdin`` polls
# (and optionally writes to) it. Their ``function_call_output`` lines
# carry the structured ``Chunk ID / Wall time / Process … / Output:``
# trailer parsed by :func:`parse_exec_command_status`.
#
# Also the **chained** subset of :data:`_SHELL_FAMILY_TOOLS`: these are
# the only shell-family tools whose output can chain across multiple
# ``function_call_output`` rows for the same call_id (the parent
# ``exec_command``'s own row plus one row per ``write_stdin`` poll,
# all rebinded by :meth:`remap_tool_result_id`). Anything else in the
# family is atomic by definition.
_EXEC_COMMAND_TOOLS = frozenset({"exec_command", "write_stdin"})

# ``event_msg.*_end`` (and ``*Response``) sub-types we still consume as
# tool_results. ``exec_command_end`` is intentionally excluded — Codex
# CLI no longer persists it (TUI sets ``persist_extended_history=false``)
# so we reconstruct the equivalent state from the chain of
# ``function_call_output`` lines instead (exec_command direct +
# write_stdin children sharing the same exec_command_id).
#
# ``web_search_end`` is also intentionally excluded: it carries a
# ``call_id`` derived from a ``WebSearchItem.id`` that the matching
# ``response_item.web_search_call`` never serialises to the JSONL, so
# the two can't be paired from disk. We instead treat ``web_search_call``
# as a resultless tool (see :data:`_RESULTLESS_TOOL_SUB_TYPES`) and let
# the ``event_msg.web_search_end`` line fall through to ``SYSTEM`` /
# ``DEBUG_ONLY`` like any other unmatched event_msg.
#
# ``image_generation_end`` is intentionally excluded too — it doesn't
# slot into the tool_use/tool_result pairing at all: the event alone
# carries everything we need (prompt, base64 PNG, saved_path) and the
# matching ``response_item.image_generation_call`` duplicates the same
# data minus the saved_path. We classify the event directly as
# :attr:`ItemKind.IMAGE` in :meth:`compute_item_kind` and the matching
# response_item falls through to ``SYSTEM`` / ``DEBUG_ONLY``.
_PERSISTED_END_EVENT_TYPES = frozenset({
    "patch_apply_end",
    "mcp_tool_call_end",
})


class ExecCommandStatus(NamedTuple):
    """Parsed status of a Codex ``function_call_output`` for an exec tool.

    Codex formats its exec_command / write_stdin tool outputs as a flat
    string with a structured trailer; we parse it once with
    :func:`parse_exec_command_status` and surface the bits we need.

    Fields:

    - ``exec_command_id``: the unified-exec process id (called ``session_id``
      by Codex itself, but we name it ``exec_command_id`` here to avoid
      colliding with TwiCC's own ``Session`` notion). Only set when the
      output reports a process *running* — the *exited* shape doesn't
      include the id, so callers resolve it via the
      ``_exec_command_maps`` cache instead.
    - ``is_terminated``: ``True`` iff a ``Process exited with code N``
      line was matched.
    - ``exit_code``: the integer code; meaningful only when
      ``is_terminated`` is ``True``.
    """
    exec_command_id: int | None
    is_terminated: bool
    exit_code: int | None


# Single-pass regex with alternation, anchored at line start (multiline
# mode). Either a "running" line or an "exited" line matches per output
# (they are mutually exclusive in Codex's formatter, see
# ``codex-rs/core/src/tools/context.rs``).
_EXEC_COMMAND_STATUS_RE = re.compile(
    r"^Process (?:running with session ID (?P<run>-?\d+)"
    r"|exited with code (?P<exit>-?\d+))$",
    re.MULTILINE,
)

# ``shell_command`` (and any other tool using
# ``format_exec_output_for_model_freeform`` in
# ``codex-rs/core/src/tools/mod.rs``) emits a freeform text trailer that
# starts with this line — anchored at line start so we never match a
# stray occurrence inside the body. The pattern is intentionally distinct
# from the ``exec_command`` trailer (``Process exited with code N``) so
# :func:`_freeform_exec_output_error` and :func:`_exit_code_error_from_output`
# can both be tried defensively without ever cross-matching.
_FREEFORM_EXIT_CODE_RE = re.compile(r"^Exit code: (-?\d+)$", re.MULTILINE)


def parse_exec_command_status(output: str) -> ExecCommandStatus:
    """Extract the status trailer from an exec_command/write_stdin output.

    Returns a default :class:`ExecCommandStatus` (``None`` / ``False`` /
    ``None``) when neither pattern is present (defensive — Codex always
    emits one when the output is well-formed).
    """
    if not isinstance(output, str) or not output:
        return ExecCommandStatus(None, False, None)
    match = _EXEC_COMMAND_STATUS_RE.search(output)
    if match is None:
        return ExecCommandStatus(None, False, None)
    if match.group("run") is not None:
        return ExecCommandStatus(int(match.group("run")), False, None)
    return ExecCommandStatus(None, True, int(match.group("exit")))


def _exit_code_error_from_output(output: str) -> str | None:
    """Render ``"Exit code N"`` for a non-zero exit, else ``None``.

    Replaces the legacy ``_exit_code_error`` helper that read
    ``payload.exit_code`` off the disappeared ``exec_command_end`` event.
    The exit code now lives in the formatted trailer of the matching
    ``function_call_output`` (parsed via :func:`parse_exec_command_status`).
    Returns ``None`` while the process is still running (``is_terminated``
    is ``False``) and on a clean exit (code ``0``).
    """
    status = parse_exec_command_status(output)
    if not status.is_terminated or status.exit_code is None or status.exit_code == 0:
        return None
    return f"Exit code {status.exit_code}"


def _freeform_exec_output_error(output: str) -> str | None:
    """Render ``"Exit code N"`` from a freeform-text shell tool output.

    Applies to every Codex tool whose ``function_call_output.output`` is
    produced by ``format_exec_output_for_model_freeform``
    (``codex-rs/core/src/tools/mod.rs``) — today: ``shell_command``. The
    wire shape is plain text starting with ::

        Exit code: N
        Wall time: X.X seconds
        [Total output lines: N]
        Output:
        <body>

    We match the first line with :data:`_FREEFORM_EXIT_CODE_RE` (anchored
    at line start, so a stray occurrence inside the body can't fool us)
    and surface ``"Exit code N"`` for a non-zero exit.

    Defensive: returns ``None`` when no match, when the captured code
    doesn't parse, or when it's zero — so the caller can chain it with
    :func:`_structured_exec_output_error` and
    :func:`_exit_code_error_from_output` and let the matching output
    shape win.
    """
    if not isinstance(output, str) or not output:
        return None
    match = _FREEFORM_EXIT_CODE_RE.search(output)
    if match is None:
        return None
    try:
        code = int(match.group(1))
    except ValueError:
        return None
    if code == 0:
        return None
    return f"Exit code {code}"


def _structured_exec_output_error(output: str) -> str | None:
    """Render ``"Exit code N"`` from a structured-JSON shell tool output.

    Applies to every Codex tool whose ``function_call_output.output`` is
    produced by ``format_exec_output_for_model_structured``
    (``codex-rs/core/src/tools/mod.rs``) — today: ``local_shell_call``
    and ``shell``. The wire shape is a JSON string
    ``{"output":"<body>","metadata":{"exit_code":N,"duration_seconds":N.N}}``,
    collapsed by ``function_tool_response`` to ``FunctionCallOutputBody::Text``
    when the inner ``InputText`` is a single item. We orjson-decode and
    pull ``metadata.exit_code`` to surface a non-zero exit the same way
    :func:`_exit_code_error_from_output` does for ``exec_command``.

    Defensive: returns ``None`` on parse failure, shape mismatch, or
    ``exit_code == 0`` so the caller can fall back to other detection
    paths (notably the legacy exec_command trailer) without crashing.
    The function is intentionally self-detecting — it doesn't take the
    parent tool name, so :meth:`extract_tool_result_info` can chain it
    with :func:`_exit_code_error_from_output` and let the matching
    output shape win.
    """
    if not isinstance(output, str) or not output:
        return None
    try:
        parsed = orjson.loads(output)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        return None
    exit_code = metadata.get("exit_code")
    if not isinstance(exit_code, int) or exit_code == 0:
        return None
    return f"Exit code {exit_code}"


def _extract_write_stdin_exec_command_id(parsed_json: dict) -> int | None:
    """Read ``arguments.session_id`` from a ``write_stdin`` function_call line.

    Codex stores function arguments as a JSON-encoded **string** on the
    tool_use line (not a nested object), so we orjson-decode them.
    The ``session_id`` field is the unified-exec process id (named
    ``exec_command_id`` everywhere on TwiCC's side). Returns ``None`` for
    malformed payloads, missing fields, or non-integer ids.
    """
    payload = _payload(parsed_json)
    if payload is None:
        return None
    raw_args = payload.get("arguments")
    if not isinstance(raw_args, str):
        return None
    try:
        args = orjson.loads(raw_args)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None
    sid = args.get("session_id")
    return sid if isinstance(sid, int) else None


def _event_msg_call_id(parsed_json: dict) -> str | None:
    """Return ``payload.call_id`` for a persisted Codex ``event_msg`` line.

    Codex's runtime emits a constellation of ``*End`` / ``*Response``
    events that carry the canonical, structured outcome of a tool call
    (``changes`` map for ``patch_apply_end``, ``CallToolResult`` for
    ``mcp_tool_call_end``, …). Each one is paired with the originating
    ``function_call`` / ``custom_tool_call`` by ``call_id``.

    Only sub-types listed in :data:`_PERSISTED_END_EVENT_TYPES` qualify;
    notably ``exec_command_end`` is excluded because the CLI no longer
    persists it. ``response_item`` lines are filtered out at the wrapper
    level. Returns the ``call_id`` for a matching event, else ``None``.
    """
    if parsed_json.get("type") != _TYPE_EVENT_MSG:
        return None
    payload = parsed_json.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") not in _PERSISTED_END_EVENT_TYPES:
        return None
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def _payload(parsed_json: dict) -> dict | None:
    """Return ``parsed_json["payload"]`` if it's a dict, else ``None``."""
    payload = parsed_json.get("payload")
    return payload if isinstance(payload, dict) else None


def _qualified_function_call_name(payload: dict) -> str:
    """Return the fully-qualified tool name for a ``function_call`` payload.

    For most tools the canonical name is just ``payload.name``. MCP tools
    additionally carry a ``payload.namespace`` (e.g.
    ``"mcp__codex_apps__github"``) — without it, the bare ``name`` (often
    starting with an underscore like ``"_search_repositories"``) is
    ambiguous and indistinguishable from any other function_call. We
    prepend the namespace with ``__`` so the resulting name keeps the
    same ``mcp__server__app__tool`` shape Claude Code's MCP tools use,
    and so ``startsWith("mcp__")`` becomes a reliable detection point in
    both backend and frontend. The frontend formatter strips leading /
    trailing ``_`` from each segment when splitting on ``__`` for
    display, so the bare-name leading underscore stays out of the
    header label.

    Returns the empty string when ``payload.name`` is missing or
    not a string — same fallback as the previous logic, so a malformed
    payload doesn't blow up the pipeline.
    """
    name = payload.get("name")
    if not isinstance(name, str):
        name = ""
    namespace = payload.get("namespace")
    if isinstance(namespace, str) and namespace:
        return f"{namespace}__{name}"
    return name


def _patch_apply_error(payload: dict) -> str | None:
    """Synthesise an error string from a ``patch_apply_end`` payload.

    Codex emits a structured ``success`` boolean alongside ``status``
    (``completed`` / ``failed`` / ``declined``) and a ``stderr`` line
    describing the failure (e.g. ``"Failed to delete file …"`` or
    ``"patch rejected by user"``). We surface that text verbatim when
    available so the front-end's error callout shows the actual
    parser/IO error, falling back to a generic label when it isn't.

    Returns ``None`` on success or when the payload isn't a
    ``patch_apply_end``.
    """
    if payload.get("type") != "patch_apply_end":
        return None
    if payload.get("success") is True and payload.get("status") == "completed":
        return None
    stderr = payload.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    if payload.get("status") == "declined":
        return "Patch declined"
    return "Patch failed"


def _mcp_tool_call_end_error(payload: dict) -> str | None:
    """Synthesise an error string from a ``mcp_tool_call_end`` payload.

    The wire shape of ``payload.result`` mirrors the Rust
    ``Result<CallToolResult, String>`` (cf. ``codex-rs/protocol/src/protocol.rs``)
    so two distinct error cases exist:

    - ``{"Err": "<message>"}`` — the invocation itself failed (transport,
      MCP server unreachable, …). The string carries a usable error
      label.
    - ``{"Ok": {"isError": true, "content": [...], ...}}`` — the
      invocation reached the server but the tool returned an error
      (``CallToolResult.is_error`` in Rust, serialised as ``isError``
      in camelCase per ``mcp.rs:138-151``). The content may carry a
      message but extracting it reliably across MCP servers is
      brittle, so we surface a generic ``"Tool error"`` label for now
      — adjust later if we see consistent shapes worth parsing.

    Returns ``None`` when the payload isn't an ``mcp_tool_call_end`` or
    when no error is reported.
    """
    if payload.get("type") != "mcp_tool_call_end":
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    if "Err" in result:
        err = result.get("Err")
        if isinstance(err, str) and err.strip():
            return err.strip()
        return "Tool error"
    ok = result.get("Ok")
    if isinstance(ok, dict) and ok.get("isError") is True:
        return "Tool error"
    return None


def _event_msg_payload_error(payload: dict) -> str | None:
    """Dispatch ``payload`` to the matching ``*_end`` error helper.

    ``patch_apply_end`` and ``mcp_tool_call_end`` expose a usable error
    signal today; ``web_search_end`` doesn't, so the helper returns
    ``None`` for it. Errors for the exec_command family are derived from
    the ``function_call_output`` text via
    :func:`_exit_code_error_from_output` instead.
    ``image_generation_end`` is classified as ``IMAGE`` (not a tool
    result) and never reaches this helper.
    """
    err = _patch_apply_error(payload)
    if err is not None:
        return err
    return _mcp_tool_call_end_error(payload)


def _count_diff_lines(unified_diff: str) -> tuple[int, int]:
    """Count ``+`` / ``-`` body lines in a unified-diff string.

    Header lines (``--- a/foo``, ``+++ b/foo``) and hunk markers
    (``@@ ...``) are ignored — only payload mutations are counted.
    Returns ``(added, removed)``.
    """
    added = 0
    removed = 0
    for line in unified_diff.splitlines():
        if not line:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _has_summary_text(reasoning_payload: dict) -> bool:
    """Return ``True`` when a ``response_item.reasoning`` payload has visible summary text.

    OpenAI publishes a summary at the model's discretion: most reasoning
    blocks come back with an empty ``summary: []`` array (no useful text
    to render), and occasionally one carries one or more
    ``{"type": "summary_text", "text": "..."}`` entries. Only the latter
    are worth rendering — the former would amount to an empty collapsible
    card and is better hidden behind DEBUG_ONLY.
    """
    summary = reasoning_payload.get("summary")
    if not isinstance(summary, list):
        return False
    for entry in summary:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "summary_text":
            continue
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            return True
    return False


def _event_msg_text(parsed_json: dict, expected_subtype: str) -> str | None:
    """Return the ``message`` string for an ``event_msg`` of the given subtype.

    Codex stores the body of ``user_message`` / ``agent_message`` events
    as a flat ``payload.message`` string — no content array, no nested
    blocks. Returns ``None`` when the wrapper or subtype doesn't match,
    or when the message is missing / empty.
    """
    if parsed_json.get("type") != _TYPE_EVENT_MSG:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != expected_subtype:
        return None
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return None


def _denied_tool_reason(session_id: str, call_id: str) -> str | None:
    """Lookup the live agent's ``_denied_tool_ids`` map for a refusal record.

    ``Provider`` is already imported at module top (used elsewhere in
    this file). Only ``get_agent_manager_registry`` is lazily imported
    to avoid a static cycle between ``compute`` and the agent package.
    Returns ``None`` cleanly if anything is missing (no live agent, no
    entry, no manager registered).
    """
    try:
        from twicc.agent.registry import get_agent_manager_registry
    except ImportError:
        return None
    try:
        manager = get_agent_manager_registry().get(Provider.CODEX)
    except Exception:
        # Registry not yet initialized (early startup, background compute
        # before the live process boots, ...).
        return None
    if manager is None:
        return None
    # The accessor is defensive: returns None if no live agent for the session.
    return manager.get_denied_tool_reason(session_id, call_id)


class CodexSessionCompute(BaseSessionCompute):
    """Concrete :class:`BaseSessionCompute` for Codex sessions.

    Classifies user/assistant messages and tool_use lines, plus pairs
    each tool_use with its output via the inherited ``ToolResultLink``
    machinery. Everything else is ``SYSTEM``.

    Carries a small per-session cache (``_exec_command_maps``) used to
    rebind ``write_stdin`` polling outputs to the parent ``exec_command``
    they belong to, since Codex CLI no longer persists the
    ``exec_command_end`` event that previously tied the chain together.
    The cache is keyed by ``Session.id`` so the singleton stays safe
    even if the watcher interleaves multiple sessions.
    :func:`get_compute` returns a per-process singleton.
    """

    provider: ClassVar[Provider] = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        # {session_id: {exec_command_id: exec_command_call_id}}.
        # Populated by :meth:`analyze_content` (batch) and
        # :meth:`extract_tool_result_info` (live) when they see a Codex
        # ``function_call_output`` for an ``exec_command`` whose trailer
        # reports a still-running unified-exec process. Read by the
        # remap hooks to resolve a ``write_stdin`` polling output back to
        # the parent ``exec_command``'s ``call_id``. Entries are cleared
        # both eagerly (when a "Process exited" status is observed) and
        # lazily (in :meth:`end_session_compute`).
        self._exec_command_maps: dict[str, dict[int, str]] = {}
        # {session_id: last seen ``info.total_token_usage.total_tokens``}.
        # Updated by :meth:`compute_item_cost_and_usage` on every
        # billable token_count event. The cumulative total advances only
        # when the LLM call actually consumed tokens, so a token_count
        # whose total matches the previous one carries no new activity:
        # it's the bootstrap (``info: null``), an inter-turn re-emission
        # (Codex republishes the previous totals at the start of a new
        # turn), or the zero-snapshot emitted alongside a compaction.
        # All three paths are filtered with a single equality check
        # against this map. Initialised by :meth:`begin_session_compute`
        # in batch mode and lazily seeded from the DB
        # (:meth:`_lookup_prev_total_tokens`) in live mode.
        self._prev_total_tokens: dict[str, int] = {}

    def _proc_map(self, session_id: str) -> dict[int, str]:
        """Return the per-session ``{exec_command_id: call_id}`` map.

        Lazily creates the map on first access — the live path may call
        the extraction hooks before any explicit :meth:`begin_session_compute`,
        so we tolerate a missing entry instead of treating it as a bug.
        """
        return self._exec_command_maps.setdefault(session_id, {})

    def begin_session_compute(self, session_id: str) -> None:
        # Reset per-session state at the start of a batch compute so a
        # previous run's leftover values can never leak into the new
        # pass. Batch always reprocesses every line of the session, so
        # starting the running total at zero is correct.
        self._exec_command_maps[session_id] = {}
        self._prev_total_tokens[session_id] = 0

    def end_session_compute(self, session_id: str) -> None:
        # Free the per-session caches after a batch compute finishes.
        # Live mode never calls this, which is fine: the exec_command
        # map is bounded by concurrent unified-exec processes (usually
        # 0–2) and entries get evicted on "Process exited"; the
        # token-count map carries one int per active session.
        self._exec_command_maps.pop(session_id, None)
        self._prev_total_tokens.pop(session_id, None)

    def _release_exec_command_for_call(
        self, session_id: str, call_id: str
    ) -> None:
        """Drop any map entry that points at ``call_id``.

        Used after observing a terminating ``Process exited`` line in
        the function_call_output chain (either the exec_command's own
        output or one of its write_stdin polls). We don't always know
        the ``exec_command_id`` (the "exited" trailer doesn't include
        it), so we scan by value — the map stays small in practice.
        """
        proc_map = self._exec_command_maps.get(session_id)
        if not proc_map:
            return
        for exec_command_id, mapped_call_id in list(proc_map.items()):
            if mapped_call_id == call_id:
                proc_map.pop(exec_command_id, None)

    def remap_tool_result_id(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str:
        """Rebind a write_stdin function_call_output to its parent exec_command.

        Codex's ``write_stdin`` tool polls (and optionally writes to) a
        unified-exec process previously spawned by ``exec_command``; its
        ``function_call_output`` therefore carries chunks of the parent
        shell's transcript, not its own. We rebind the result row so it
        lands on the parent's ``ToolResultLink`` chain, keyed by the
        exec_command's call_id.

        The chain is resolved through ``self._exec_command_maps``, which
        :meth:`analyze_content` populated when it saw the parent
        exec_command's first ``Process running with session ID N`` line.
        Falls back to identity when the chain can't be resolved
        (malformed write_stdin payload, missing map entry, …) so the
        link still gets created — just under the naive id.

        Also handles eviction for the write_stdin side: when this poll's
        output reports a terminating ``Process exited``, the entry is
        removed from the map AFTER we resolved the parent_call_id, so
        analyze_content's reading order stays correct (it had already
        populated / read the map by the time we got here).
        """
        parent = tool_use_map.get(naive_tool_use_id)
        if parent is None or parent.tool_name != "write_stdin":
            return naive_tool_use_id
        exec_command_id = _extract_write_stdin_exec_command_id(parent.parsed_json)
        if exec_command_id is None:
            return naive_tool_use_id
        proc_map = self._exec_command_maps.get(session_id)
        if not proc_map:
            return naive_tool_use_id
        parent_call_id = proc_map.get(exec_command_id, naive_tool_use_id)
        # Evict the entry on a terminating poll so any stray future
        # write_stdin against the same id doesn't latch onto a stale
        # call_id (Codex would never reissue, but defensive cleanup is
        # cheap).
        payload = _payload(parsed_json)
        if payload is not None:
            output = payload.get("output", "")
            if (
                isinstance(output, str)
                and parse_exec_command_status(output).is_terminated
            ):
                proc_map.pop(exec_command_id, None)
        return parent_call_id

    def remap_tool_result_id_live(
        self,
        parsed_json: dict,  # noqa: ARG002 (parent is identified from the candidate row)
        naive_tool_use_id: str,
        *,
        session_id: str,
        item: SessionItem,
    ) -> str:
        """Live equivalent of :meth:`remap_tool_result_id` (no in-memory map).

        Since live mode doesn't carry a ``tool_use_map``, we resolve the
        chain through two DB lookups: first the write_stdin function_call
        line (to read its ``arguments.session_id``), then the
        exec_command function_call_output that announced the same
        unified-exec process id.

        The cost is incurred only on a write_stdin's result line, which
        is rare per session — falls back to identity at every step that
        can't be resolved so other tools' result rows are unaffected.
        """
        parent_payload = self._lookup_write_stdin_call_payload(
            session_id, item.line_num, naive_tool_use_id
        )
        if parent_payload is None:
            return naive_tool_use_id
        raw_args = parent_payload.get("arguments")
        if not isinstance(raw_args, str):
            return naive_tool_use_id
        try:
            args = orjson.loads(raw_args)
        except orjson.JSONDecodeError:
            return naive_tool_use_id
        if not isinstance(args, dict):
            return naive_tool_use_id
        exec_command_id = args.get("session_id")
        if not isinstance(exec_command_id, int):
            return naive_tool_use_id
        return self._lookup_exec_command_call_id(
            session_id, item.line_num, exec_command_id, naive_tool_use_id
        )

    def _lookup_write_stdin_call_payload(
        self, session_id: str, max_line_num: int, naive_tool_use_id: str
    ) -> dict | None:
        """Find the function_call payload for a write_stdin id, or ``None``.

        Returns the payload dict only when the candidate is a
        ``function_call`` named ``write_stdin`` matching the given
        call_id — anything else (including non-write_stdin tool_uses, or
        text containing the id) yields ``None`` so callers can fall
        through to identity remap.
        """
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains=naive_tool_use_id,
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None:
                continue
            if payload.get("type") != "function_call":
                continue
            if payload.get("call_id") != naive_tool_use_id:
                continue
            if payload.get("name") != "write_stdin":
                return None
            return payload
        return None

    def _lookup_exec_command_call_id(
        self,
        session_id: str,
        max_line_num: int,
        exec_command_id: int,
        fallback: str,
    ) -> str:
        """Resolve the exec_command call_id that owns ``exec_command_id``.

        Searches for the function_call_output line carrying the
        ``Process running with session ID <exec_command_id>`` marker —
        that line's ``call_id`` IS the parent exec_command's call_id
        (Codex routes the response through the same identifier).
        Returns ``fallback`` when nothing is found, so the live link is
        still created (just under the naive id).
        """
        marker = f"Process running with session ID {exec_command_id}"
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains=marker,
        ).order_by('line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None:
                continue
            if payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                continue
            call_id = payload.get("call_id")
            if isinstance(call_id, str) and call_id:
                return call_id
        return fallback

    def _maintain_exec_command_map(
        self,
        session_id: str,
        call_id: str,
        payload: dict,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str | None:
        """Update the per-session exec_command map and surface an error string.

        Called from :meth:`analyze_content` for every Codex
        ``function_call_output`` / ``custom_tool_call_output``. Looks up
        the parent tool_use in ``tool_use_map`` to identify
        exec_command / write_stdin lines and updates the map for the
        ``exec_command`` side only:

        - On an exec_command output reporting ``Process running with
          session ID N``, register ``map[N] = call_id`` so future
          write_stdin children can be remapped to this exec_command.
        - On an exec_command output reporting ``Process exited``,
          evict any entry that points to this call_id (covers both the
          synchronous one-shot — no entry to evict — and the long-running
          parent's own final poll).

        write_stdin's contribution to the map is handled in
        :meth:`remap_tool_result_id` instead, so the eviction happens
        AFTER the orchestrator has read the parent_call_id from the map
        for the pairing.

        Returns the synthesised ``"Exit code N"`` error string for a
        non-zero exit (or ``None`` otherwise) so the caller can stuff it
        into ``ContentAnalysis.tool_result_error``.
        """
        parent = tool_use_map.get(call_id)
        if parent is None or parent.tool_name not in _EXEC_COMMAND_TOOLS:
            return None
        output = payload.get("output", "")
        if not isinstance(output, str):
            output = ""
        if parent.tool_name == "exec_command":
            status = parse_exec_command_status(output)
            proc_map = self._proc_map(session_id)
            if status.exec_command_id is not None and not status.is_terminated:
                proc_map[status.exec_command_id] = call_id
            elif status.is_terminated:
                self._release_exec_command_for_call(session_id, call_id)
        return _exit_code_error_from_output(output)

    # ------------------------------------------------------------------
    # Extraction — content classification
    # ------------------------------------------------------------------

    def transform_inline(self, parsed_json: dict) -> str | None:
        # No inline rewrites for Codex: the JSONL format is already in
        # its canonical shape (no legacy XML to normalise).
        return None

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        # NOTE: any change to this classification MUST bump
        # CODEX_COMPUTE_VERSION so existing sessions are recomputed.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)

        # ``compacted`` is the top-level wrapper Codex CLI writes when
        # auto-compacting the rolling context. We pick this one (rather
        # than the redundant ``event_msg.context_compacted`` event)
        # because the payload carries a future-proof ``encrypted_content``
        # — if Codex ever ships a readable summary in there, the item is
        # already at the right kind/display level to surface it. Today
        # the content is opaque so the frontend renders a placeholder.
        if wrapper_type == _TYPE_COMPACTED:
            return ItemKind.COMPACT_SUMMARY

        if wrapper_type == _TYPE_EVENT_MSG and payload is not None:
            sub_type = payload.get("type")
            if sub_type == _PAYLOAD_USER_MESSAGE:
                return ItemKind.USER_MESSAGE
            if sub_type == _PAYLOAD_AGENT_MESSAGE:
                return ItemKind.ASSISTANT_MESSAGE
            # ``image_generation_end`` is a standalone visible row, not a
            # tool_result. Its payload carries the base64 PNG, the revised
            # prompt and the on-disk path — everything the frontend needs
            # to render the image inline. The matching
            # ``response_item.image_generation_call`` duplicates the same
            # data (minus saved_path) and falls through to SYSTEM below.
            if sub_type == _PAYLOAD_IMAGE_GENERATION_END:
                return ItemKind.IMAGE
            # event_msg lines whose sub-type is in
            # :data:`_PERSISTED_END_EVENT_TYPES` are tool_result End
            # events. Kind stays ``None`` so the base falls into the
            # ``is_tool_result_item`` branch (-> DEBUG_ONLY).
            if _event_msg_call_id(parsed_json) is not None:
                return None

        if wrapper_type == _TYPE_RESPONSE_ITEM and payload is not None:
            sub_type = payload.get("type")
            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                # ``write_stdin`` doesn't get its own tool card —
                # its result chunks are rebound to the parent
                # ``exec_command``'s ``ToolResultLink`` chain by
                # :meth:`remap_tool_result_id`.
                if (
                    sub_type == "function_call"
                    and payload.get("name") in _NON_TOOL_FUNCTION_NAMES
                ):
                    return ItemKind.SYSTEM
                return ItemKind.TOOL_USE
            # Tool-result-bearing response_item lines: kind stays None
            # so the base routes via ``is_tool_result_item`` to
            # DEBUG_ONLY without also tagging them as plain SYSTEM.
            if sub_type in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            # Reasoning lines are rendered only when the model produced an
            # actual summary block — the encrypted_content is opaque to us
            # so a reasoning whose ``summary`` is empty has nothing visible
            # to show. We bucket the empty case back to SYSTEM (-> DEBUG_ONLY)
            # via the fall-through below; the non-empty case becomes its own
            # COLLAPSIBLE kind so it joins tool_use et al. in the group
            # machinery and gets a dedicated frontend renderer.
            if sub_type == "reasoning" and _has_summary_text(payload):
                return ItemKind.REASONING

        # Everything else (session_meta, turn_context, other response_item
        # subtypes — message/reasoning-without-summary/…, other event_msg
        # subtypes without call_id including ``event_msg.context_compacted``,
        # malformed lines) is bucketed as SYSTEM and ends up at
        # DEBUG_ONLY display level.
        return ItemKind.SYSTEM

    # compute_item_display_level + compute_item_metadata: inherited from base.
    # USER_MESSAGE/ASSISTANT_MESSAGE → ALWAYS, SYSTEM → DEBUG_ONLY,
    # TOOL_USE → COLLAPSIBLE (default fall-through), tool-result lines
    # whose kind is None → DEBUG_ONLY via :meth:`is_tool_result_item`.

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        # Every Codex JSONL line carries a top-level ISO 8601 ``timestamp``.
        timestamp = parsed_json.get("timestamp")
        if isinstance(timestamp, str):
            return parse_timestamp_to_datetime(timestamp)
        return None

    # extract_title_from_user_message: inherited from base
    # (calls extract_user_message_text, then strip_markdown + truncate).

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        # Title extraction reads the first user_message's plain text.
        # event_msg:user_message stores the human input as a flat
        # string, optionally with images alongside (irrelevant for the
        # title).
        return _event_msg_text(parsed_json, _PAYLOAD_USER_MESSAGE)

    # ------------------------------------------------------------------
    # Extraction — out-of-scope hooks (V1 stubs)
    # ------------------------------------------------------------------
    #
    # These hooks all return empty / no-op values so the inherited
    # machinery (group state, batch orchestration, watcher live sync)
    # still runs without errors. Each one will get a real implementation
    # when the matching Codex feature lands (tools, costs, runtime env, ...).

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        # ``slug`` is out of scope (Codex doesn't expose one). Three line
        # shapes contribute to runtime fields:
        #
        # - ``session_meta`` (opening line, one per session) carries the
        #   initial ``payload.cwd`` and ``payload.git.branch``. The latter
        #   is captured as a stable historical fallback for
        #   ``cwd_git_branch`` — filesystem-based resolution can drift
        #   (worktree gone, branch renamed since) (cf. the matching
        #   ``Session.cwd_git_branch`` comment).
        # - ``turn_context`` (emitted on every turn) carries
        #   ``payload.cwd`` and ``payload.model``. The base orchestrator's
        #   "last non-null wins" rule means a mid-session ``cd`` updates
        #   ``Session.cwd`` and a model swap updates ``Session.model``.
        #   ``turn_context`` does NOT carry git info — ``cwd_git_branch``
        #   keeps its initial value from ``session_meta``; the resolved
        #   ``Session.git_directory`` / ``Session.git_branch`` get
        #   re-derived from the new ``cwd`` downstream by the base.
        # - ``event_msg.task_started`` (emitted alongside every new turn)
        #   carries ``payload.model_context_window`` — Codex's published
        #   compaction threshold, equal to 95% of the model's nominal
        #   input window. We divide it back by
        #   :data:`_TASK_STARTED_WINDOW_HEADROOM_FACTOR` and snap to the
        #   nearest 1000 to recover the nominal window (272K for
        #   ``gpt-5.x``: advertised 400K total = 272K input + 128K
        #   output reserved), then surface it as ``context_max`` so the
        #   base loop can write it onto ``Session.context_max``. This
        #   gives us a real window value for sessions imported from
        #   JSONL (and a tracking value if the user switches to a model
        #   with a different window mid-session).
        cwd: str | None = None
        cwd_git_branch: str | None = None
        model: str | None = None
        context_max: int | None = None
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is not None:
            if wrapper_type == _TYPE_SESSION_META:
                value = payload.get("cwd")
                if isinstance(value, str) and value:
                    cwd = value
                git_info = payload.get("git")
                if isinstance(git_info, dict):
                    branch = git_info.get("branch")
                    if isinstance(branch, str) and branch:
                        cwd_git_branch = branch
            elif wrapper_type == _TYPE_TURN_CONTEXT:
                value = payload.get("cwd")
                if isinstance(value, str) and value:
                    cwd = value
                value = payload.get("model")
                if isinstance(value, str) and value:
                    model = value
            elif (
                wrapper_type == _TYPE_EVENT_MSG
                and payload.get("type") == _PAYLOAD_TASK_STARTED
            ):
                window = payload.get("model_context_window")
                if isinstance(window, int) and window > 0:
                    # Recover the nominal window from Codex's published
                    # 95%-of-nominal value, then snap to the nearest
                    # 1000 so we get the round numbers a user expects
                    # in the UI (272_000 instead of 271_999, etc.) and
                    # tolerate small drift if Codex changes its rounding.
                    nominal = window / _TASK_STARTED_WINDOW_HEADROOM_FACTOR
                    context_max = round(nominal / 1000) * 1000
        return {
            "cwd": cwd,
            "cwd_git_branch": cwd_git_branch,
            "model": model,
            "slug": None,
            "context_max": context_max,
        }

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],  # noqa: ARG002 (Codex dedups via total_tokens, not message_id)
        current_model: str | None,
    ) -> None:
        """Assign ``cost`` and ``context_usage`` for Codex billing items.

        Only ``event_msg.token_count`` lines carry usage data — every
        other JSONL shape returns immediately. For matching lines the
        algorithm is:

        1. Skip lines whose ``info`` is null (the bootstrap snapshot
           emitted before the first LLM call) or malformed.
        2. Read ``info.total_token_usage.total_tokens`` and compare to
           the previous value tracked in ``self._prev_total_tokens``.
           When the cumulative total hasn't moved, this token_count is
           non-billable:

           - inter-turn re-emission (Codex republishes the previous
             totals at the start of a new turn so its UI has the latest
             snapshot before any new call lands);
           - compaction-zero (a ``last_token_usage`` of ``0/0/0``
             emitted alongside the ``compacted`` event).

           Both are filtered by the same equality check.
        3. Otherwise, advance the running total, convert
           ``last_token_usage`` to the cross-provider :class:`TokenUsage`
           via :func:`to_token_usage`, and assign ``context_usage`` plus
           (when a current model and a timestamp are known) ``cost``.

        The cumulative ``total_token_usage`` itself is **never** read
        for billing — every call to :func:`calculate_line_cost` works
        off the per-event ``last_token_usage``. The total only acts as
        a monotonic clock for the dedup test above.

        Live mode never calls :meth:`begin_session_compute`, so the
        first time a session shows up here we lazy-seed
        ``self._prev_total_tokens[session_id]`` from the DB via
        :meth:`_lookup_prev_total_tokens` to avoid double-counting an
        inter-turn re-emission that happens to be the first line of the
        live batch.
        """
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != _PAYLOAD_TOKEN_COUNT:
            return
        info = payload.get("info")
        if not isinstance(info, dict):
            return  # bootstrap snapshot (info: null), no billable activity
        total_usage = info.get("total_token_usage")
        if not isinstance(total_usage, dict):
            return
        cur_total = total_usage.get("total_tokens", 0) or 0

        session_id = item.session_id
        if session_id not in self._prev_total_tokens:
            # Live mode: seed the running total from the most recent
            # already-processed token_count in the DB so dedup works on
            # the first batch line.
            self._prev_total_tokens[session_id] = self._lookup_prev_total_tokens(
                session_id, item.line_num,
            )

        if cur_total == self._prev_total_tokens[session_id]:
            return  # no new billable activity (re-emission / compaction-zero)
        self._prev_total_tokens[session_id] = cur_total

        last_usage = info.get("last_token_usage")
        if not isinstance(last_usage, dict):
            return

        token_usage = to_token_usage(last_usage)
        item.context_usage = calculate_line_context_usage(token_usage)

        if not current_model or item.timestamp is None:
            return  # cost requires both an active model and a date
        if extract_model_info(current_model) is None:
            return  # unrecognised model name — no fallback bucket
        from twicc.providers.helpers import get_provider_helpers
        helpers = get_provider_helpers(Provider.CODEX)
        model_id = f"{helpers.OPENROUTER_MODEL_PREFIX}{current_model}"
        item.cost = helpers.calculate_line_cost(
            token_usage, model_id, item.timestamp.date(),
        )

    def _lookup_prev_total_tokens(
        self, session_id: str, current_line_num: int,
    ) -> int:
        """Return the latest already-processed ``total_tokens`` for the session.

        Walks ``SessionItem`` rows of ``session_id`` whose ``line_num``
        is below ``current_line_num``, in reverse, and returns the
        ``info.total_token_usage.total_tokens`` of the first parseable
        ``event_msg.token_count`` found. Returns ``0`` when the session
        has no prior token_count (genuinely first event of a fresh
        session) — then any subsequent token_count with a non-zero
        total advances the dedup cursor as expected.

        Used only by the live path; batch mode resets to ``0`` via
        :meth:`begin_session_compute`. The scan stops at the first hit,
        so it costs at most one row read on a healthy session.
        """
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=current_line_num,
            content__contains='"type":"token_count"',
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_EVENT_MSG:
                continue
            payload = _payload(parsed)
            if payload is None or payload.get("type") != _PAYLOAD_TOKEN_COUNT:
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total_usage = info.get("total_token_usage")
            if not isinstance(total_usage, dict):
                continue
            return total_usage.get("total_tokens", 0) or 0
        return 0

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        # Two line shapes carry a tool_result for Codex:
        # - ``response_item`` with a ``*_call_output`` payload (the LLM-facing
        #   string returned from the function call). For exec_command
        #   shells this is the chunked transcript; for write_stdin it's
        #   one chunk of the parent exec_command's transcript (rebound
        #   via :meth:`remap_tool_result_id`).
        # - ``event_msg`` whose sub-type is in
        #   :data:`_PERSISTED_END_EVENT_TYPES` (patch_apply_end,
        #   mcp_tool_call_end). They carry the structured outcome of
        #   the tool call and are paired with the originating function_call
        #   by ``call_id``. ``web_search_end`` and ``image_generation_end``
        #   are intentionally absent (see their set's docstring).
        # Both are routed to DEBUG_ONLY; the front uses the tool's
        # ``isToolRunning`` hook to know when the chain is complete.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return False
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            return payload.get("type") in _TOOL_RESULT_PAYLOAD_TYPES
        if wrapper_type == _TYPE_EVENT_MSG:
            return _event_msg_call_id(parsed_json) is not None
        return False

    def extract_tool_use_entries(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility; future remap may use it)
    ) -> dict[str, str]:
        # One tool_use per JSONL line in Codex (no nesting like Claude),
        # so the returned mapping has at most one entry. Keyed by the
        # OpenAI ``call_id`` — that's what the matching output also carries.
        # ``write_stdin`` is included here even though its
        # :meth:`compute_item_kind` returns ``SYSTEM`` (no tool card):
        # we still need its call_id in ``tool_use_map`` so
        # :meth:`remap_tool_result_id` can recognise its
        # ``function_call_output`` and rebind it to the parent
        # ``exec_command``'s call_id.
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return _EMPTY_TOOL_USE_ENTRIES
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_TOOL_USE_ENTRIES
        sub_type = payload.get("type")
        if sub_type not in _TOOL_CALL_PAYLOAD_TYPES:
            return _EMPTY_TOOL_USE_ENTRIES
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return _EMPTY_TOOL_USE_ENTRIES
        native_name = _NATIVE_TOOL_NAME_BY_SUB_TYPE.get(sub_type)
        if native_name is not None:
            return {call_id: native_name}
        return {call_id: _qualified_function_call_name(payload)}

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict | None = None,  # noqa: ARG002
    ) -> ToolResultInfo | None:
        # Mirror of ``extract_tool_use_entries`` for the matching result
        # line. Two shapes contribute:
        # - response_item.{function_call_output, custom_tool_call_output}
        #   — the LLM-facing output string. Three error-detection paths
        #   coexist here, all guarded by their own shape so they're
        #   mutually exclusive in practice:
        #     * ``local_shell_call`` / ``shell`` outputs are a JSON
        #       string carrying ``{"output":..., "metadata":{"exit_code":N,
        #       ...}}`` (cf. ``format_exec_output_for_model_structured``
        #       in ``codex-rs/core/src/tools/mod.rs``) —
        #       :func:`_structured_exec_output_error` decodes it and
        #       surfaces ``"Exit code N"`` for a non-zero exit.
        #     * ``shell_command`` outputs carry a freeform text trailer
        #       starting with ``Exit code: N`` (cf.
        #       ``format_exec_output_for_model_freeform``) —
        #       :func:`_freeform_exec_output_error` handles it.
        #     * ``exec_command`` / ``write_stdin`` outputs carry a Codex
        #       formatted trailer with a ``Process exited with code N``
        #       line — :func:`_exit_code_error_from_output` handles it.
        #     * everything else has no exit signal here, so all three
        #       helpers return ``None`` and ``error_text`` stays ``None``.
        # - event_msg.* whose sub-type is in
        #   :data:`_PERSISTED_END_EVENT_TYPES` (patch_apply_end,
        #   mcp_tool_call_end). Both shapes coexist as separate
        #   ``ToolResultLink`` rows for the same call_id (no dedup);
        #   the front knows whether to wait for both via
        #   ``getExpectedResultCount``.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return None
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            if payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            call_id = payload.get("call_id")
            output = payload.get("output", "")
            if isinstance(output, str):
                error_text = (
                    _structured_exec_output_error(output)
                    or _freeform_exec_output_error(output)
                    or _exit_code_error_from_output(output)
                )
            else:
                error_text = None
        elif wrapper_type == _TYPE_EVENT_MSG:
            call_id = _event_msg_call_id(parsed_json)
            error_text = _event_msg_payload_error(payload)
        else:
            return None
        if not isinstance(call_id, str) or not call_id:
            return None

        # 4th error source: the live agent's _denied_tool_ids map.
        # Codex's function_call_output line carries the rejection text in
        # ``output`` ("exec_command failed for ... Rejected(...)" /
        # "aborted by user after X.Xs") but no is_error flag. We don't
        # pattern-match the text — we consult the agent-side map populated
        # at WS-response time by ``CodexAgent._record_decision_outcome``.
        # If the user refused, the recorded reason supersedes any
        # exit-code text that ``_*_error`` helpers might have produced.
        denied_reason = _denied_tool_reason(session_id, call_id)
        if denied_reason is not None:
            error_text = denied_reason

        return ToolResultInfo(
            tool_use_id=call_id,
            is_error=error_text is not None,
            error_text=error_text,
        )

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        return None

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        return _EMPTY_TASK_TOOL_USES

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        return []

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        # Codex only exposes absolute file paths through
        # ``event_msg.patch_apply_end.changes`` (a ``{abs_path: change_entry}``
        # map). The matching ``custom_tool_call name=apply_patch`` ships
        # its patch as raw Lark grammar with paths that may be relative,
        # and ``exec_command`` / ``write_stdin`` carry arbitrary shell
        # text — neither is a reliable source for git resolution. So
        # only ``patch_apply_end`` rows contribute paths here, and any
        # session that doesn't apply a patch falls back on the cwd-based
        # git resolution in the orchestrator (see ``compute_base``).
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return _EMPTY_FILE_PATHS
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "patch_apply_end":
            return _EMPTY_FILE_PATHS
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            return _EMPTY_FILE_PATHS
        return [p for p in changes if isinstance(p, str) and p.startswith("/")]

    def compute_link_extra(
        self, parsed_json: dict, tool_name: str
    ) -> str | None:
        """Return the JSON ``ToolResultLink.extra`` payload for this result.

        Two shapes contribute today:

        - ``exec_command`` / ``write_stdin`` ``function_call_output``
          rows whose trailer reports ``Process exited`` produce
          ``{"is_terminated": true}``. Other rows in the same chain
          (still-running polls, the synchronous one-shot's own running
          status, the parent's first chunk) return ``None`` so the
          tool_state's ``Max``-aggregated ``extra`` only flips to
          terminated once we've seen the closing chunk.
        - ``apply_patch`` ``event_msg.patch_apply_end`` rows produce
          ``{"lines_added": N, "lines_removed": M, "files": [...]}``
          so the front can show the per-tool badge.

        Returns ``None`` everywhere else (most rows don't need an
        ``extra`` payload).

        Output JSON shapes (``orjson.dumps`` of the dict):

        For exec_command / write_stdin completion::

            {"is_terminated": true}

        For apply_patch::

            {
                # Aggregated totals across every entry in ``changes``.
                "lines_added":   <int>,    # always present
                "lines_removed": <int>,    # always present (0 when only adds)

                # Per-file breakdown, in the order ``changes.items()``
                # iterates (i.e. insertion order from the Codex JSONL).
                # ``path`` is the absolute path Codex applied the patch
                # to. Always present, even for a single-file call.
                "files": [
                    {
                        "path":          <str>,
                        "lines_added":   <int>,
                        "lines_removed": <int>,
                    },
                    ...
                ],
            }

        Per-entry counting rules:

        - ``update``: ``+`` / ``-`` body lines of ``unified_diff``
          (header / hunk-marker lines are excluded by
          :func:`_count_diff_lines`).
        - ``add``: every line of ``content`` counts as ``+1``.
        - ``delete``: every line of ``content`` counts as ``-1``.

        The frontend reads ``lines_added`` / ``lines_removed`` for the
        per-tool ``+N -M`` summary badge; the per-file breakdown is
        provided for future surfaces (it is not consumed yet today).
        """
        # Shell family: ``is_terminated`` flagged on the closing chunk
        # for chained tools (``exec_command`` / ``write_stdin``), and
        # immediately on arrival for atomic tools (everything else in
        # :data:`_SHELL_FAMILY_TOOLS`). Listing only the chained set
        # explicitly means every new shell-like tool added to the
        # family defaults to atomic — see the comments on those two
        # frozensets above.
        if tool_name in _SHELL_FAMILY_TOOLS:
            if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
                return None
            payload = _payload(parsed_json)
            if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            if tool_name in _EXEC_COMMAND_TOOLS:
                # Chained: a closing chunk is recognised by the
                # ``Process exited with code N`` line in the Codex
                # unified-exec status trailer. While the process is
                # still running we explicitly emit nothing so the
                # ``Max``-aggregated extra stays at its starting value
                # and the frontend keeps the spinner alive.
                output = payload.get("output", "")
                if not isinstance(output, str):
                    return None
                if not parse_exec_command_status(output).is_terminated:
                    return None
            # Atomic result row, or the closing chunk of a chained
            # sequence — flag it so the card stops spinning.
            return orjson.dumps({"is_terminated": True}).decode()

        if tool_name != "apply_patch":
            return None
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return None
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "patch_apply_end":
            return None
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes:
            return None

        lines_added = 0
        lines_removed = 0
        files: list[dict] = []
        for path, entry in changes.items():
            if not isinstance(entry, dict) or not isinstance(path, str):
                continue
            file_added = 0
            file_removed = 0
            change_type = entry.get("type")
            if change_type == "update":
                unified_diff = entry.get("unified_diff")
                if isinstance(unified_diff, str):
                    file_added, file_removed = _count_diff_lines(unified_diff)
            elif change_type == "add":
                content = entry.get("content")
                if isinstance(content, str) and content:
                    file_added = content.count("\n") + (
                        0 if content.endswith("\n") else 1
                    )
            elif change_type == "delete":
                content = entry.get("content")
                if isinstance(content, str) and content:
                    file_removed = content.count("\n") + (
                        0 if content.endswith("\n") else 1
                    )

            files.append({
                "path": path,
                "lines_added": file_added,
                "lines_removed": file_removed,
            })
            lines_added += file_added
            lines_removed += file_removed

        if not files:
            return None
        return orjson.dumps({
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files": files,
        }).decode()

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        # Codex user_message / agent_message events carry their text in
        # a single flat ``message`` string (no mixed content blocks),
        # so they never have a collapsible prefix or suffix.
        return False, False

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        return False

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        return None

    # ------------------------------------------------------------------
    # Batch compute
    # ------------------------------------------------------------------

    def analyze_content(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> ContentAnalysis:
        # Line shapes that contribute to content analysis in Codex:
        # - ``event_msg.user_message`` / ``event_msg.agent_message`` carry
        #   plain text.
        # - ``event_msg.*`` whose sub-type is in
        #   :data:`_PERSISTED_END_EVENT_TYPES` is a tool_result End event
        #   paired by ``call_id`` with the originating function_call.
        # - ``response_item.function_call`` / ``custom_tool_call`` declares
        #   a tool_use. ``write_stdin`` lands in ``tool_use_map`` here so
        #   :meth:`remap_tool_result_id` can later rebind its output to
        #   the parent ``exec_command``; it stays bucketed as ``SYSTEM``
        #   for rendering via :meth:`compute_item_kind`.
        # - ``response_item.{function_call_output, custom_tool_call_output}``
        #   is a tool_result. For exec_command / write_stdin lines we
        #   also (a) parse the trailer to derive an error string from
        #   the formatted ``Process exited with code N`` line, and
        #   (b) maintain ``self._exec_command_maps[session_id]`` —
        #   adding an entry on ``Process running with session ID N`` and
        #   evicting on a terminating exit so the remap hook can resolve
        #   write_stdin children to their parent exec_command.
        # Every other line falls through to the empty analysis.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_ANALYSIS

        # Top-level ``compacted`` wrapper: classified as COMPACT_SUMMARY
        # by :meth:`compute_item_kind`. The encrypted summary is opaque
        # so we don't surface a ``text_content`` body, but the row is
        # still visible (the frontend renders a placeholder).
        if wrapper_type == _TYPE_COMPACTED:
            return ContentAnalysis(
                has_visible_content=True,
                text_content=None,
                is_system_xml=False,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=False,
                has_suffix=False,
                tool_result_agent_info=None,
            )

        if wrapper_type == _TYPE_EVENT_MSG:
            sub_type = payload.get("type")
            if sub_type in (_PAYLOAD_USER_MESSAGE, _PAYLOAD_AGENT_MESSAGE):
                message = payload.get("message")
                text = message.strip() if isinstance(message, str) else None
                return ContentAnalysis(
                    has_visible_content=bool(text),
                    text_content=text,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            # ``image_generation_end`` is a standalone visible row (see
            # :meth:`compute_item_kind`). No tool_result pairing, no text
            # content surfaced here — the frontend pulls ``revised_prompt``,
            # ``result`` and ``saved_path`` straight from the payload via
            # the ImageGeneration component.
            if sub_type == _PAYLOAD_IMAGE_GENERATION_END:
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            event_call_id = _event_msg_call_id(parsed_json)
            if event_call_id is not None:
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=event_call_id,
                    tool_result_error=_event_msg_payload_error(payload),
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            return _EMPTY_ANALYSIS

        if wrapper_type == _TYPE_RESPONSE_ITEM:
            sub_type = payload.get("type")

            # Resultless tool calls (``web_search_call``) have no
            # ``call_id`` and never pair with anything — short-circuit
            # to a visible TOOL_USE row with no tool_use_entries so the
            # frontend renders the card without waiting for a result.
            if sub_type in _RESULTLESS_TOOL_SUB_TYPES:
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return _EMPTY_ANALYSIS

            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                native_name = _NATIVE_TOOL_NAME_BY_SUB_TYPE.get(sub_type)
                if native_name is not None:
                    name = native_name
                else:
                    name = _qualified_function_call_name(payload)
                tool_use_entries = {call_id: name}
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=tool_use_entries,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            if sub_type in _TOOL_RESULT_PAYLOAD_TYPES:
                # For exec_command / write_stdin outputs, parse the
                # formatted trailer to (a) maintain the per-session
                # ``exec_command_id`` map and (b) surface a
                # ``"Exit code N"`` error string so the front lights up
                # the same way it would for any other failed shell.
                tool_result_error = self._maintain_exec_command_map(
                    session_id, call_id, payload, tool_use_map
                )
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=call_id,
                    tool_result_error=tool_result_error,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

        return _EMPTY_ANALYSIS

    # compute_session_metadata + apply_session_complete: inherited from base.
    # The base orchestrates DB I/O and dispatches every parsing hook
    # declared above. ``sync_session_items_from_file`` (also inherited) is
    # driven by ``CodexSessionsWatcher`` for live updates.


# =============================================================================
# Singleton accessor
# =============================================================================


_compute_instance: CodexSessionCompute | None = None


def get_compute() -> CodexSessionCompute:
    """Return the process-local :class:`CodexSessionCompute` singleton."""
    global _compute_instance
    if _compute_instance is None:
        _compute_instance = CodexSessionCompute()
    return _compute_instance
