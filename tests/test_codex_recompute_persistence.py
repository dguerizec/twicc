"""Regression test: background re-compute must not erase ToolResultLink.error.

Scenario
--------
1. The live path (watcher) processes a Codex JSONL line where the user denied
   a tool. It creates a ``ToolResultLink`` row with
   ``error="User denied this action"`` by consulting
   ``CodexAgent._denied_tool_ids`` (in-memory map).
2. The backend restarts. ``_denied_tool_ids`` is gone; ``_denied_tool_reason``
   returns ``None`` for every call_id.
3. Background re-compute reprocesses the same JSONL. ``analysis.tool_result_error``
   is ``None`` (no live agent). The old diff loop would detect a difference
   (original.error="User denied this action" vs new.error=None) and call
   ``bulk_update`` with ``error=None``, silently erasing the refusal reason.
4. After the fix the diff loop preserves non-None ``error`` / ``extra`` from
   the original row when the re-compute produces ``None``.

This test drives ``compute_session_metadata`` → ``apply_session_complete`` end
to end using real Codex JSONL that produces a ``function_call`` / ``function_call_output``
pair. The initial ``ToolResultLink`` is pre-seeded with ``error="User denied this action"``
to simulate the live path. After re-compute the row must still carry the original error.
"""

from __future__ import annotations

import json
import queue
from datetime import datetime, timezone

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, ToolResultLink
from twicc.providers.codex.compute import get_compute


# ---------------------------------------------------------------------------
# Minimal Codex JSONL helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _codex_line(type_: str, payload_type: str, payload_extra: dict | None = None, **top) -> str:
    """Return a single Codex JSONL line (JSON string)."""
    payload = {"type": payload_type, **(payload_extra or {})}
    line = {
        "timestamp": _NOW.isoformat(),
        "type": type_,
        "payload": payload,
        **top,
    }
    return json.dumps(line)


def _function_call_line(call_id: str, name: str = "exec_command") -> str:
    """``response_item.function_call`` — creates a TOOL_USE item."""
    return _codex_line(
        "response_item",
        "function_call",
        payload_extra={"call_id": call_id, "name": name, "arguments": "{}"},
    )


def _function_call_output_line(call_id: str, output: str = "ok") -> str:
    """``response_item.function_call_output`` — creates a tool_result item."""
    return _codex_line(
        "response_item",
        "function_call_output",
        payload_extra={"call_id": call_id, "output": output},
    )


# ---------------------------------------------------------------------------
# Helpers shared with test_group_logic.py
# ---------------------------------------------------------------------------


def _apply_compute_results(result_queue, provider_compute) -> None:
    """Drain the queue and apply every ``session_complete`` message."""
    from queue import Empty

    while True:
        try:
            raw_msg = result_queue.get_nowait()
        except Empty:
            break
        msg = orjson.loads(raw_msg)
        if msg.get("type") == "session_complete":
            provider_compute.apply_session_complete(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def codex_session(db):
    """Create a minimal Project + Session for Codex."""
    project = Project.objects.create(id="test-project-codex")
    session = Session.objects.create(
        id="test-session-codex",
        project=project,
        provider=Provider.CODEX,
    )
    return session


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestRecomputePreservesToolResultLinkError:
    """Background re-compute must not overwrite a live-path-recorded error."""

    def test_existing_error_not_erased_on_recompute(self, codex_session):
        """
        Pre-seed a ToolResultLink with error="User denied this action".
        Run batch compute without a live agent (error=None from _denied_tool_reason).
        Verify the error is preserved after apply_session_complete.
        """
        session = codex_session
        call_id = "call-test-denied-001"

        # 1. Create the two JSONL items: tool_use (function_call) + tool_result
        #    (function_call_output with a benign "aborted by user" text that
        #    none of the static helpers pick up as an error — they only parse
        #    exit codes, not freeform refusal text).
        tool_use_content = _function_call_line(call_id, name="exec_command")
        tool_result_content = _function_call_output_line(
            call_id,
            output="aborted by user after 0.0s",  # no exit code → helpers return None
        )

        tool_use_item = SessionItem.objects.create(
            session=session,
            line_num=1,
            content=tool_use_content,
        )
        tool_result_item = SessionItem.objects.create(
            session=session,
            line_num=2,
            content=tool_result_content,
        )

        # 2. Pre-seed the ToolResultLink as the live path would have created it,
        #    with error="User denied this action" (recorded by _denied_tool_ids).
        ToolResultLink.objects.create(
            session=session,
            tool_use_line_num=tool_use_item.line_num,
            tool_result_line_num=tool_result_item.line_num,
            tool_use_id=call_id,
            tool_name="exec_command",
            tool_result_at=_NOW,
            error="User denied this action",
            extra=json.dumps({"is_terminated": True}),
        )

        # 3. Run background re-compute (no live agent → _denied_tool_reason
        #    returns None → error=None in computed result).
        compute = get_compute()
        result_q = queue.Queue()
        compute.compute_session_metadata(session.id, result_q)
        _apply_compute_results(result_q, compute)

        # 4. The ToolResultLink row must still carry the original values.
        link = ToolResultLink.objects.get(
            session=session,
            tool_use_id=call_id,
            tool_result_line_num=tool_result_item.line_num,
        )
        assert link.error == "User denied this action", (
            "Background re-compute erased the live-path refusal reason. "
            f"Got: {link.error!r}"
        )
        assert link.extra == json.dumps({"is_terminated": True}), (
            "Background re-compute erased the live-path extra payload. "
            f"Got: {link.extra!r}"
        )

    def test_recompute_creates_new_row_when_none_exists(self, codex_session):
        """
        If no ToolResultLink existed before (e.g. initial sync skipped it),
        the batch re-compute should create the row normally.
        """
        session = codex_session
        call_id = "call-test-new-001"

        SessionItem.objects.create(
            session=session,
            line_num=3,
            content=_function_call_line(call_id, name="exec_command"),
        )
        SessionItem.objects.create(
            session=session,
            line_num=4,
            content=_function_call_output_line(call_id, output="ok"),
        )

        # No pre-existing ToolResultLink.
        assert not ToolResultLink.objects.filter(session=session, tool_use_id=call_id).exists()

        compute = get_compute()
        result_q = queue.Queue()
        compute.compute_session_metadata(session.id, result_q)
        _apply_compute_results(result_q, compute)

        link = ToolResultLink.objects.filter(
            session=session,
            tool_use_id=call_id,
        ).first()
        assert link is not None, "Batch compute should have created a ToolResultLink for the new pair"
        # No error expected for a clean 'ok' output with no exit code trailer.
        assert link.error is None
