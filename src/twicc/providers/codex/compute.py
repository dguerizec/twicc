"""
Compute pipeline for Codex sessions.

V1 — minimal classification only. Each Codex JSONL line is wrapped in
``{timestamp, type, payload}``; this pass turns the wrapper into a
TwiCC :class:`~twicc.core.enums.ItemKind`:

- ``event_msg`` with ``payload.type == "user_message"`` → ``USER_MESSAGE``
- ``event_msg`` with ``payload.type == "agent_message"`` → ``ASSISTANT_MESSAGE``
- everything else (``session_meta``, ``turn_context``, every
  ``response_item``, every other ``event_msg``, ``compacted``) →
  ``SYSTEM`` (so it lands at ``DEBUG_ONLY`` display level).

Token counts, costs, runtime environment fields (cwd / model / git
branch), tool calls, tool results, custom titles, session-start
detection, file-change stats and subagent linkage are all explicitly
out of scope for this version. Their hooks return empty / no-op
values so the inherited base machinery (group state, batch compute,
title extraction) still runs cleanly.
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
_PAYLOAD_USER_MESSAGE = "user_message"
_PAYLOAD_AGENT_MESSAGE = "agent_message"


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

    V1 only classifies user/assistant content and leaves every other
    kind of line as ``SYSTEM``. The class is stateless;
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
        if parsed_json.get("type") == _TYPE_EVENT_MSG:
            payload = _payload(parsed_json)
            if payload is not None:
                sub_type = payload.get("type")
                if sub_type == _PAYLOAD_USER_MESSAGE:
                    return ItemKind.USER_MESSAGE
                if sub_type == _PAYLOAD_AGENT_MESSAGE:
                    return ItemKind.ASSISTANT_MESSAGE

        # Everything else (session_meta, turn_context, response_item,
        # other event_msg subtypes, compacted, malformed lines) is
        # bucketed as SYSTEM and ends up at DEBUG_ONLY display level.
        return ItemKind.SYSTEM

    # compute_item_display_level + compute_item_metadata: inherited from base
    # (USER_MESSAGE/ASSISTANT_MESSAGE → ALWAYS, SYSTEM → DEBUG_ONLY).

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
        # No tool_result handling in V1: every response_item:* line
        # (including function_call_output) is currently SYSTEM, which
        # already routes to DEBUG_ONLY without needing this hook.
        return False

    def extract_tool_use_entries(self, parsed_json: dict) -> dict[str, str]:
        return _EMPTY_TOOL_USE_ENTRIES

    def extract_tool_result_info(self, parsed_json: dict) -> ToolResultInfo | None:
        return None

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
        # Only user_message / agent_message contribute visible content
        # in V1. Everything else returns the shared empty analysis.
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return _EMPTY_ANALYSIS
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_ANALYSIS

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
