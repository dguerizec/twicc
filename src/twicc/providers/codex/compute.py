"""
Compute pipeline for Codex sessions.

Each Codex JSONL line is wrapped in ``{timestamp, type, payload}``; this
pass turns the wrapper into a TwiCC :class:`~twicc.core.enums.ItemKind`
and, for tool calls, lets the inherited base orchestration build the
``ToolResultLink`` rows that pair a call with its result.

Classification rules (any change MUST bump CODEX_COMPUTE_VERSION):

- ``event_msg.user_message`` → ``USER_MESSAGE``
- ``event_msg.agent_message`` → ``ASSISTANT_MESSAGE``
- ``event_msg.*`` with a non-empty ``payload.call_id`` (any ``*End`` /
  ``*Response`` event — exec_command_end, patch_apply_end,
  mcp_tool_call_end, web_search_end, image_generation_end,
  collab_*_end, dynamic_tool_call_response, …) → kind stays ``None``;
  routed to ``DEBUG_ONLY`` via :meth:`is_tool_result_item`. Pairs with
  the matching function_call by ``call_id`` — these events carry the
  structured outcome of the tool (full aggregated transcript,
  exit_code, ``changes`` map, ``CallToolResult``, …), richer than the
  LLM-facing ``function_call_output``. Both shapes coexist as
  :class:`ToolResultLink` rows for the same tool_use; the front uses
  ``getExpectedResultCount`` to know it must wait for both before
  marking the tool as done.
- ``response_item.function_call`` / ``custom_tool_call`` → ``TOOL_USE``
  (-> ``COLLAPSIBLE``), except ``function_call name=write_stdin`` which
  is bucketed as ``SYSTEM`` (its trace is captured by the matching
  ``exec_command``'s ``exec_command_end``).
- ``response_item.{function_call_output, custom_tool_call_output}`` →
  kind stays ``None`` (-> ``DEBUG_ONLY``). Pairs as a tool_result —
  the LLM-facing string is the first of (potentially) two links per
  tool_use_id, with the matching event_msg.*_end as the second.
- everything else (``session_meta``, ``turn_context``, other
  ``response_item`` subtypes, other ``event_msg`` subtypes without
  ``call_id``, ``compacted``) → ``SYSTEM`` (lands at ``DEBUG_ONLY``).

The ``call_id`` carried by every line above is the pairing key,
stored as-is in ``ToolResultLink.tool_use_id`` (analogous to Claude's
``tool_use_id``).

Token counts, costs, runtime environment fields (cwd / model / git
branch), custom titles, session-start detection, file-change stats and
subagent linkage are still out of scope at this stage. Their hooks
return empty / no-op values so the inherited base machinery (group
state, batch compute, title extraction) still runs cleanly.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import SessionItem
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    ToolResultInfo,
    parse_timestamp_to_datetime,
)


# Keys at the wrapper level. Every Codex JSONL line is
# ``{"timestamp": ..., "type": ..., "payload": {...}}`` so we always
# go through ``payload`` to reach Codex-specific fields.
_TYPE_EVENT_MSG = "event_msg"
_TYPE_RESPONSE_ITEM = "response_item"
_PAYLOAD_USER_MESSAGE = "user_message"
_PAYLOAD_AGENT_MESSAGE = "agent_message"

# response_item payload sub-types that represent a tool call. Each is its
# own JSONL line (mono-block), unlike Claude where tool_uses live inside a
# message.content array. ``function_call`` is the standard OpenAI form;
# ``custom_tool_call`` is the freeform variant used for tools whose input
# isn't JSON (apply_patch ships its patch as raw Lark-grammar text).
_TOOL_CALL_PAYLOAD_TYPES = frozenset({"function_call", "custom_tool_call"})

# Function-call ``name`` values whose tool_use is bucketed as SYSTEM (no
# tool card rendered) because the relevant exchange is captured elsewhere.
# ``write_stdin`` belongs to a previously-spawned ``exec_command`` session;
# the aggregated transcript ends up in that exec_command's eventual
# ``exec_command_end`` event_msg (same call_id as the original exec).
_NON_TOOL_FUNCTION_NAMES = frozenset({"write_stdin"})

# Tool-result payload sub-types from ``response_item`` lines (the
# LLM-facing string returned to the model). Paired with the calls above
# by ``call_id`` and routed to DEBUG_ONLY via :meth:`is_tool_result_item`.
_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})


def _event_msg_call_id(parsed_json: dict) -> str | None:
    """Return ``payload.call_id`` for a persisted Codex ``event_msg`` line.

    Codex's runtime emits a constellation of ``*End`` / ``*Response``
    events that carry the canonical, structured outcome of a tool call
    (full ``aggregated_output`` for ``exec_command_end``, ``changes``
    map for ``patch_apply_end``, ``CallToolResult`` for
    ``mcp_tool_call_end``, …). Every one of them is paired with the
    originating ``function_call`` / ``custom_tool_call`` by ``call_id``.

    ``rollout/src/policy.rs`` only persists the ``*End`` shape (the
    matching ``*Begin`` events are dropped before the rollout is
    written), so in practice any persisted ``event_msg`` carrying a
    non-empty ``call_id`` is a tool_result End event — independent of
    its concrete sub-type. Returns the ``call_id`` if matched, else
    ``None``. Kept as a module-level helper to keep the discovery rule
    in one place.
    """
    if parsed_json.get("type") != _TYPE_EVENT_MSG:
        return None
    payload = parsed_json.get("payload")
    if not isinstance(payload, dict):
        return None
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def _payload(parsed_json: dict) -> dict | None:
    """Return ``parsed_json["payload"]`` if it's a dict, else ``None``."""
    payload = parsed_json.get("payload")
    return payload if isinstance(payload, dict) else None


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


class CodexSessionCompute(BaseSessionCompute):
    """Concrete :class:`BaseSessionCompute` for Codex sessions.

    Classifies user/assistant messages and tool_use lines, plus pairs
    each tool_use with its output via the inherited ``ToolResultLink``
    machinery. Everything else is ``SYSTEM``. The class is stateless;
    :func:`get_compute` returns a per-process singleton.
    """

    provider: ClassVar[Provider] = Provider.CODEX

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

        if wrapper_type == _TYPE_EVENT_MSG and payload is not None:
            sub_type = payload.get("type")
            if sub_type == _PAYLOAD_USER_MESSAGE:
                return ItemKind.USER_MESSAGE
            if sub_type == _PAYLOAD_AGENT_MESSAGE:
                return ItemKind.ASSISTANT_MESSAGE
            # Any persisted event_msg with a ``call_id`` is a tool_result
            # End/Response event (see :func:`_event_msg_call_id`).
            # Kind stays ``None`` so the base falls into the
            # ``is_tool_result_item`` branch (-> DEBUG_ONLY).
            if _event_msg_call_id(parsed_json) is not None:
                return None

        if wrapper_type == _TYPE_RESPONSE_ITEM and payload is not None:
            sub_type = payload.get("type")
            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                # Some function_call names don't deserve their own tool
                # card (their meaningful trace is captured by another
                # tool's event_msg.exec_command_end via the same call_id).
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

        # Everything else (session_meta, turn_context, other response_item
        # subtypes — message/reasoning/…, other event_msg subtypes
        # without call_id, ``compacted``, malformed lines) is bucketed
        # as SYSTEM and ends up at DEBUG_ONLY display level.
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
    # machinery (group state, batch orchestration, watcher live sync —
    # the latter is not wired yet anyway) still runs without errors.
    # Each one will get a real implementation when the matching
    # Codex feature lands (tools, costs, runtime env, ...).

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        # cwd / cwd_git_branch / model / slug are out of scope for V1.
        return {
            "cwd": None,
            "cwd_git_branch": None,
            "model": None,
            "slug": None,
        }

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],
    ) -> None:
        # No cost / context_usage assignment in V1. Codex emits
        # token_count event_msgs but mapping them onto items + computing
        # USD cost from OpenAI prices is a later step.
        return None

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        # Two line shapes carry a tool_result for Codex:
        # - ``response_item`` with a ``*_call_output`` payload (the LLM-facing
        #   string returned from the function call).
        # - ``event_msg`` with a non-empty ``call_id`` — any of the
        #   ``*End`` / ``*Response`` events (exec_command_end, patch_apply_end,
        #   mcp_tool_call_end, web_search_end, image_generation_end,
        #   collab_*_end, dynamic_tool_call_response, …). They carry the
        #   structured outcome of the tool call and are paired with the
        #   originating function_call by the ``call_id``.
        # Both are routed to DEBUG_ONLY here and each gets its own
        # ``ToolResultLink`` row when they arrive — they coexist for the
        # same call_id (no replacement). The front uses the wrapper +
        # tool name to know how many results to wait for via
        # ``getExpectedResultCount`` before flipping the spinner off.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return False
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            return payload.get("type") in _TOOL_RESULT_PAYLOAD_TYPES
        if wrapper_type == _TYPE_EVENT_MSG:
            return _event_msg_call_id(parsed_json) is not None
        return False

    def extract_tool_use_entries(self, parsed_json: dict) -> dict[str, str]:
        # One tool_use per JSONL line in Codex (no nesting like Claude),
        # so the returned mapping has at most one entry. Keyed by the
        # OpenAI ``call_id`` — that's what the matching output also carries.
        # Only function_call names that we actually render as tool cards
        # contribute here; ``write_stdin`` is bucketed as SYSTEM upstream
        # so its call_id never enters ``tool_use_map`` and thus never
        # gets a ToolResultLink.
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return _EMPTY_TOOL_USE_ENTRIES
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") not in _TOOL_CALL_PAYLOAD_TYPES:
            return _EMPTY_TOOL_USE_ENTRIES
        call_id = payload.get("call_id")
        name = payload.get("name")
        if not isinstance(call_id, str) or not call_id:
            return _EMPTY_TOOL_USE_ENTRIES
        if payload.get("type") == "function_call" and name in _NON_TOOL_FUNCTION_NAMES:
            return _EMPTY_TOOL_USE_ENTRIES
        return {call_id: name if isinstance(name, str) else ""}

    def extract_tool_result_info(self, parsed_json: dict) -> ToolResultInfo | None:
        # Mirror of ``extract_tool_use_entries`` for the matching result
        # line. Two shapes contribute:
        # - response_item.{function_call_output, custom_tool_call_output}
        #   — the LLM-facing output string.
        # - event_msg.* with a non-empty ``call_id`` — any persisted
        #   ``*End`` / ``*Response`` event (exec_command_end,
        #   patch_apply_end, mcp_tool_call_end, …). When both shapes
        #   arrive for the same call_id they each become their own
        #   ``ToolResultLink`` row (no dedup); the front knows whether
        #   to wait for both via ``getExpectedResultCount``.
        # No error detection yet (``is_error=False, error_text=None``).
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return None
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            if payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            call_id = payload.get("call_id")
        elif wrapper_type == _TYPE_EVENT_MSG:
            call_id = _event_msg_call_id(parsed_json)
        else:
            return None
        if not isinstance(call_id, str) or not call_id:
            return None
        return ToolResultInfo(
            tool_use_id=call_id,
            is_error=False,
            error_text=None,
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
        return _EMPTY_FILE_PATHS

    def compute_file_change_stats(
        self, parsed_json: dict, tool_name: str
    ) -> str | None:
        return None

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

    def analyze_content(self, parsed_json: dict) -> ContentAnalysis:
        # Line shapes that contribute to content analysis in Codex:
        # - ``event_msg.user_message`` / ``event_msg.agent_message`` carry
        #   plain text.
        # - ``event_msg.*`` with a non-empty ``call_id`` (see
        #   :func:`_event_msg_call_id`) is a tool_result End/Response
        #   event paired by ``call_id`` with the originating function_call.
        # - ``response_item.function_call`` / ``custom_tool_call`` declares
        #   a tool_use (except ``write_stdin``, which is bucketed as
        #   SYSTEM and contributes nothing here).
        # - ``response_item.{function_call_output, custom_tool_call_output}``
        #   is a tool_result. When an event_msg counterpart for the same
        #   ``call_id`` exists, both shapes coexist as separate
        #   ``ToolResultLink`` rows (no replacement). The front decides
        #   when the tool is done via ``getExpectedResultCount``.
        # Every other line falls through to the empty analysis.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_ANALYSIS

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

            event_call_id = _event_msg_call_id(parsed_json)
            if event_call_id is not None:
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=event_call_id,
                    tool_result_error=None,
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
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return _EMPTY_ANALYSIS

            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                name = payload.get("name")
                # write_stdin doesn't get a tool card — keep the line
                # invisible to ``tool_use_map`` so neither it nor any
                # later line will pair against its call_id.
                if sub_type == "function_call" and name in _NON_TOOL_FUNCTION_NAMES:
                    return _EMPTY_ANALYSIS
                tool_use_entries = {call_id: name if isinstance(name, str) else ""}
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
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=call_id,
                    tool_result_error=None,
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
    # declared above.

    # ------------------------------------------------------------------
    # Watcher live sync — not wired for Codex yet
    # ------------------------------------------------------------------
    #
    # ``sync_session_items_from_file`` is inherited from the base, but
    # CodexOrchestrator does not start a JSONL watcher today, so it
    # never runs in practice. New lines reach the DB only through the
    # next ``initial_sync`` (i.e. after a TwiCC restart).


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
