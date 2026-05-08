"""
Compute pipeline for Codex sessions.

Each Codex JSONL line is wrapped in ``{timestamp, type, payload}``; this
pass turns the wrapper into a TwiCC :class:`~twicc.core.enums.ItemKind`
and, for tool calls, lets the inherited base orchestration build the
``ToolResultLink`` rows that pair a call with its output.

- ``event_msg`` with ``payload.type == "user_message"`` → ``USER_MESSAGE``
- ``event_msg`` with ``payload.type == "agent_message"`` → ``ASSISTANT_MESSAGE``
- ``response_item`` with ``payload.type ∈ {"function_call",
  "custom_tool_call"}`` → ``TOOL_USE`` (-> ``COLLAPSIBLE``)
- ``response_item`` with ``payload.type ∈ {"function_call_output",
  "custom_tool_call_output"}`` → kind stays ``None`` so the base routes
  via :meth:`is_tool_result_item` to ``DEBUG_ONLY``; the line is then
  surfaced under its tool_use through ``ToolResultLink``.
- everything else (``session_meta``, ``turn_context``, other
  ``response_item`` subtypes, other ``event_msg`` subtypes,
  ``compacted``) → ``SYSTEM`` (lands at ``DEBUG_ONLY``).

The ``call_id`` carried by ``function_call`` / ``function_call_output``
plays the role of Claude's ``tool_use_id`` and is stored as-is in the
``ToolResultLink.tool_use_id`` column.

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

# Matching tool-result payload types, paired with the calls above by
# ``call_id``. They are routed to DEBUG_ONLY via :meth:`is_tool_result_item`
# and surfaced under their tool_use through ``ToolResultLink``.
_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})


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

        if wrapper_type == _TYPE_EVENT_MSG:
            payload = _payload(parsed_json)
            if payload is not None:
                sub_type = payload.get("type")
                if sub_type == _PAYLOAD_USER_MESSAGE:
                    return ItemKind.USER_MESSAGE
                if sub_type == _PAYLOAD_AGENT_MESSAGE:
                    return ItemKind.ASSISTANT_MESSAGE

        if wrapper_type == _TYPE_RESPONSE_ITEM:
            payload = _payload(parsed_json)
            if payload is not None:
                sub_type = payload.get("type")
                if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                    return ItemKind.TOOL_USE
                # Tool-result lines: kind stays None so the base
                # ``compute_item_display_level`` falls into the
                # ``is_tool_result_item`` branch (-> DEBUG_ONLY) without
                # also tagging them as plain SYSTEM.
                if sub_type in _TOOL_RESULT_PAYLOAD_TYPES:
                    return None

        # Everything else (session_meta, turn_context, other response_item
        # subtypes — message/reasoning/…, other event_msg subtypes,
        # compacted, malformed lines) is bucketed as SYSTEM and ends up
        # at DEBUG_ONLY display level.
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
        # ``response_item`` lines whose payload type is a tool-result
        # variant (function_call_output / custom_tool_call_output). They
        # are routed to DEBUG_ONLY here and surfaced under their tool_use
        # via ``ToolResultLink``.
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return False
        payload = _payload(parsed_json)
        if payload is None:
            return False
        return payload.get("type") in _TOOL_RESULT_PAYLOAD_TYPES

    def extract_tool_use_entries(self, parsed_json: dict) -> dict[str, str]:
        # One tool_use per JSONL line in Codex (no nesting like Claude),
        # so the returned mapping has at most one entry. Keyed by the
        # OpenAI ``call_id`` — that's what the matching output also carries.
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return _EMPTY_TOOL_USE_ENTRIES
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") not in _TOOL_CALL_PAYLOAD_TYPES:
            return _EMPTY_TOOL_USE_ENTRIES
        call_id = payload.get("call_id")
        name = payload.get("name")
        if not isinstance(call_id, str) or not call_id:
            return _EMPTY_TOOL_USE_ENTRIES
        return {call_id: name if isinstance(name, str) else ""}

    def extract_tool_result_info(self, parsed_json: dict) -> ToolResultInfo | None:
        # Mirror of ``extract_tool_use_entries`` for the matching output
        # line. V1: no error detection (the base is happy with
        # is_error=False, error_text=None — ``ToolResultLink`` is created
        # purely from the call_id pairing).
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return None
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
            return None
        call_id = payload.get("call_id")
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
        # Three line shapes contribute to content analysis in Codex:
        # ``event_msg.user_message`` / ``event_msg.agent_message`` carry
        # plain text; ``response_item.function_call`` /
        # ``response_item.custom_tool_call`` declare a tool_use; their
        # ``*_output`` siblings declare a tool_result. Every other line
        # falls through to the empty analysis.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_ANALYSIS

        if wrapper_type == _TYPE_EVENT_MSG:
            sub_type = payload.get("type")
            if sub_type not in (_PAYLOAD_USER_MESSAGE, _PAYLOAD_AGENT_MESSAGE):
                return _EMPTY_ANALYSIS

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

        if wrapper_type == _TYPE_RESPONSE_ITEM:
            sub_type = payload.get("type")
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return _EMPTY_ANALYSIS

            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                name = payload.get("name")
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
                # V1: no error detection. The base creates a
                # ``ToolResultLink`` from ``tool_result_id`` regardless.
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
