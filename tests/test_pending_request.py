"""
Tests for PendingRequest dataclass, ProcessInfo serialization,
and ClaudeProcess pending request mechanism.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

# Mock context object with a suggestions attribute (mimics ToolPermissionContext)
_EMPTY_CONTEXT = SimpleNamespace(suggestions=[])

from twicc.providers.claude_code.agent.manager import ProcessManager
from twicc.providers.claude_code.agent.process import ClaudeProcess
from twicc.providers.claude_code.agent.states import (
    PendingRequest,
    ProcessInfo,
    ProcessState,
    serialize_process_info,
)


def _make_process_info(**kwargs) -> ProcessInfo:
    """Create a ProcessInfo with sensible defaults, overridable via kwargs."""
    defaults = {
        "session_id": "test-session",
        "project_id": "test-project",
        "state": ProcessState.ASSISTANT_TURN,
        "previous_state": None,
        "started_at": 1000000.0,
        "state_changed_at": 1000001.0,
        "last_activity": 1000002.0,
    }
    defaults.update(kwargs)
    return ProcessInfo(**defaults)


def _make_pending_request(**kwargs) -> PendingRequest:
    """Create a PendingRequest with sensible defaults, overridable via kwargs."""
    defaults = {
        "request_id": "req-123",
        "request_type": "tool_approval",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la", "description": "List files"},
        "created_at": 1000005.0,
    }
    defaults.update(kwargs)
    return PendingRequest(**defaults)


async def _dummy_get_slug(session_id: str) -> str:
    """Return a random slug for testing."""
    return f"test-slug-{uuid.uuid4().hex[:8]}"


async def _dummy_on_cron_created(session_id, cron_id, cron_expr, recurring, prompt, created_at, next_fire):
    """No-op cron created callback for testing."""


async def _dummy_on_cron_deleted(session_id, cron_id):
    """No-op cron deleted callback for testing."""


def _make_claude_process(session_id: str = "test-session-1") -> ClaudeProcess:
    """Create a ClaudeProcess for testing, without starting it."""
    return ClaudeProcess(
        session_id=session_id,
        project_id="test-project-1",
        cwd="/tmp/test",
        permission_mode="default",
        selected_model=None,
        effort=None,
        thinking_enabled=None,
        get_session_slug=_dummy_get_slug,
        on_cron_created=_dummy_on_cron_created,
        on_cron_deleted=_dummy_on_cron_deleted,
    )


def _inject_pending(process: ClaudeProcess, request: PendingRequest, future: asyncio.Future | None = None) -> asyncio.Future:
    """Directly inject a pending request + Future on a process (test helper)."""
    if future is None:
        future = asyncio.get_event_loop().create_future()
    process._pending_requests[request.request_id] = request
    process._pending_futures[request.request_id] = future
    return future


def _make_manager_with_process(
    session_id: str = "session-1",
    state: ProcessState = ProcessState.ASSISTANT_TURN,
    pending_request: PendingRequest | None = None,
    last_activity: float | None = None,
    state_changed_at: float | None = None,
    inject_future: bool = False,
) -> tuple[ProcessManager, ClaudeProcess, asyncio.Future | None]:
    """Create a ProcessManager with a single mock process injected directly.

    Returns (manager, process, future). The future is non-None when a pending
    request was injected.
    """
    manager = ProcessManager()
    process = _make_claude_process(session_id=session_id)
    process.state = state
    process._state_change_callback = AsyncMock()
    future: asyncio.Future | None = None
    if pending_request is not None:
        future = _inject_pending(process, pending_request) if inject_future else None
        process._pending_requests[pending_request.request_id] = pending_request
        if inject_future and future is None:
            future = asyncio.get_event_loop().create_future()
            process._pending_futures[pending_request.request_id] = future
    if last_activity is not None:
        process.last_activity = last_activity
    if state_changed_at is not None:
        process.state_changed_at = state_changed_at
    manager._processes[session_id] = process
    return manager, process, future


# =============================================================================
# PendingRequest dataclass
# =============================================================================


class TestPendingRequest:
    """Tests for the PendingRequest dataclass."""

    def test_tool_approval_creation(self):
        """PendingRequest can be created for a tool approval request."""
        req = PendingRequest(
            request_id="abc-123",
            request_type="tool_approval",
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/test", "description": "Delete test directory"},
            created_at=1234567890.0,
        )
        assert req.request_id == "abc-123"
        assert req.request_type == "tool_approval"
        assert req.tool_name == "Bash"
        assert req.tool_input == {"command": "rm -rf /tmp/test", "description": "Delete test directory"}
        assert req.created_at == 1234567890.0

    def test_ask_user_question_creation(self):
        """PendingRequest can be created for an ask_user_question request."""
        questions = [
            {
                "question": "How should I format the output?",
                "header": "Format",
                "options": [
                    {"label": "Summary", "description": "Brief overview"},
                    {"label": "Detailed", "description": "Full explanation"},
                ],
                "multiSelect": False,
            }
        ]
        req = PendingRequest(
            request_id="def-456",
            request_type="ask_user_question",
            tool_name="AskUserQuestion",
            tool_input={"questions": questions},
            created_at=1234567891.0,
        )
        assert req.request_type == "ask_user_question"
        assert req.tool_name == "AskUserQuestion"
        assert len(req.tool_input["questions"]) == 1
        assert req.tool_input["questions"][0]["question"] == "How should I format the output?"

    def test_frozen(self):
        """PendingRequest is frozen (immutable)."""
        req = _make_pending_request()
        try:
            req.request_id = "new-id"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass


# =============================================================================
# ProcessInfo with pending_requests
# =============================================================================


class TestProcessInfoWithPendingRequests:
    """Tests for PendingRequest integration in ProcessInfo."""

    def test_pending_requests_defaults_to_empty_tuple(self):
        """ProcessInfo.pending_requests defaults to an empty tuple."""
        info = _make_process_info()
        assert info.pending_requests == ()

    def test_pending_requests_can_hold_multiple(self):
        """ProcessInfo can hold multiple pending requests."""
        req1 = _make_pending_request(request_id="r1", tool_name="Read")
        req2 = _make_pending_request(request_id="r2", tool_name="Glob")
        info = _make_process_info(pending_requests=(req1, req2))
        assert len(info.pending_requests) == 2
        assert info.pending_requests[0].request_id == "r1"
        assert info.pending_requests[1].request_id == "r2"


# =============================================================================
# serialize_process_info() with pending_requests
# =============================================================================


class TestSerializeProcessInfoPendingRequests:
    """Tests for pending_requests serialization in serialize_process_info()."""

    def test_no_pending_requests_omits_key(self):
        """When pending_requests is empty, the serialized dict has no 'pending_requests' key."""
        info = _make_process_info()
        data = serialize_process_info(info)
        assert "pending_requests" not in data

    def test_single_tool_approval_serialization(self):
        """A single tool approval request is serialized as a one-element list."""
        req = PendingRequest(
            request_id="uuid-abc",
            request_type="tool_approval",
            tool_name="Bash",
            tool_input={"command": "echo hello", "description": "Print hello"},
            created_at=1000005.0,
        )
        info = _make_process_info(pending_requests=(req,))
        data = serialize_process_info(info)

        assert "pending_requests" in data
        assert isinstance(data["pending_requests"], list)
        assert len(data["pending_requests"]) == 1
        pr = data["pending_requests"][0]
        assert pr["request_id"] == "uuid-abc"
        assert pr["request_type"] == "tool_approval"
        assert pr["tool_name"] == "Bash"
        assert pr["tool_input"] == {"command": "echo hello", "description": "Print hello"}
        assert pr["created_at"] == 1000005.0

    def test_multiple_requests_preserve_order(self):
        """Multiple pending requests are serialized in the same order they appear."""
        req1 = _make_pending_request(request_id="r1", tool_name="Read", created_at=1000.0)
        req2 = _make_pending_request(request_id="r2", tool_name="Glob", created_at=1001.0)
        info = _make_process_info(pending_requests=(req1, req2))
        data = serialize_process_info(info)

        assert [pr["request_id"] for pr in data["pending_requests"]] == ["r1", "r2"]
        assert [pr["tool_name"] for pr in data["pending_requests"]] == ["Read", "Glob"]

    def test_ask_user_question_serialization(self):
        """Ask user question pending request is fully serialized."""
        questions = [
            {
                "question": "Which format?",
                "header": "Output",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            }
        ]
        req = PendingRequest(
            request_id="uuid-def",
            request_type="ask_user_question",
            tool_name="AskUserQuestion",
            tool_input={"questions": questions},
            created_at=1000006.0,
        )
        info = _make_process_info(pending_requests=(req,))
        data = serialize_process_info(info)

        pr = data["pending_requests"][0]
        assert pr["request_type"] == "ask_user_question"
        assert pr["tool_name"] == "AskUserQuestion"
        assert pr["tool_input"]["questions"] == questions

    def test_serialized_pending_request_has_exactly_five_keys(self):
        """The serialized pending request dict contains exactly the five expected keys."""
        req = _make_pending_request()
        info = _make_process_info(pending_requests=(req,))
        data = serialize_process_info(info)

        pr = data["pending_requests"][0]
        assert set(pr.keys()) == {"request_id", "request_type", "tool_name", "tool_input", "created_at"}

    def test_permission_suggestions_included_when_present(self):
        """The optional permission_suggestions key is included when set."""
        suggestions = [{"type": "addRules", "rules": [{"toolName": "Read", "ruleContent": "/x/**"}], "behavior": "allow"}]
        req = _make_pending_request(permission_suggestions=suggestions)
        info = _make_process_info(pending_requests=(req,))
        data = serialize_process_info(info)
        assert data["pending_requests"][0]["permission_suggestions"] == suggestions

    def test_other_fields_unaffected_by_pending_requests(self):
        """Adding pending_requests does not change serialization of other fields."""
        info_without = _make_process_info(error="some error", kill_reason="manual")
        info_with = _make_process_info(
            error="some error",
            kill_reason="manual",
            pending_requests=(_make_pending_request(),),
        )

        data_without = serialize_process_info(info_without)
        data_with = serialize_process_info(info_with)

        for key in data_without:
            assert data_with[key] == data_without[key]

        assert set(data_with.keys()) - set(data_without.keys()) == {"pending_requests"}


# =============================================================================
# ClaudeProcess._handle_pending_request()
# =============================================================================


class TestHandlePendingRequest:
    """Tests for ClaudeProcess._handle_pending_request()."""

    def test_creates_pending_request_and_blocks_on_future(self):
        """_handle_pending_request() registers a request, notifies state change,
        then blocks on its Future. After resolution, the request is removed and
        a second notification fires."""
        process = _make_claude_process()
        state_change_calls = []

        async def mock_state_change(proc):
            state_change_calls.append(tuple(proc.pending_requests))

        process._state_change_callback = mock_state_change

        async def run():
            task = asyncio.create_task(
                process._handle_pending_request(
                    "Bash", {"command": "ls"}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)

            # Exactly one in-flight request, with the expected fields
            assert len(process._pending_requests) == 1
            request_id, req = next(iter(process._pending_requests.items()))
            assert req.request_type == "tool_approval"
            assert req.tool_name == "Bash"
            assert req.tool_input == {"command": "ls"}
            assert req.request_id == request_id  # request_id field matches the dict key

            # Future exists and is unresolved
            future = process._pending_futures[request_id]
            assert not future.done()

            # First state change fired with the request present
            assert len(state_change_calls) == 1
            assert len(state_change_calls[0]) == 1
            assert state_change_calls[0][0].tool_name == "Bash"

            # Resolve the Future
            response = PermissionResultAllow(updated_input={"command": "ls"})
            future.set_result(response)

            result = await task

            # After resolution: dicts are empty
            assert process._pending_requests == {}
            assert process._pending_futures == {}

            # Second notification fired with no requests
            assert len(state_change_calls) == 2
            assert state_change_calls[1] == ()

            assert result is response

        asyncio.run(run())

    def test_ask_user_question_sets_correct_type(self):
        """_handle_pending_request() sets request_type to 'ask_user_question'
        when tool_name is 'AskUserQuestion'."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            questions = [{"question": "Which format?", "options": [{"label": "JSON"}]}]
            task = asyncio.create_task(
                process._handle_pending_request(
                    "AskUserQuestion", {"questions": questions}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)

            assert len(process._pending_requests) == 1
            req = next(iter(process._pending_requests.values()))
            assert req.request_type == "ask_user_question"
            assert req.tool_name == "AskUserQuestion"

            # Resolve to clean up
            future = next(iter(process._pending_futures.values()))
            future.set_result(PermissionResultAllow(updated_input={"questions": questions}))
            await task

        asyncio.run(run())

    def test_non_ask_user_question_tools_are_tool_approval(self):
        """_handle_pending_request() sets request_type to 'tool_approval'
        for any tool other than 'AskUserQuestion'."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            for tool_name in ("Bash", "Write", "Edit", "Read"):
                task = asyncio.create_task(
                    process._handle_pending_request(
                        tool_name, {"file_path": "/test"}, _EMPTY_CONTEXT
                    )
                )
                await asyncio.sleep(0)

                req = next(iter(process._pending_requests.values()))
                assert req.request_type == "tool_approval"
                assert req.tool_name == tool_name

                future = next(iter(process._pending_futures.values()))
                future.set_result(PermissionResultAllow(updated_input={}))
                await task

        asyncio.run(run())


# =============================================================================
# ClaudeProcess.resolve_pending_request()
# =============================================================================


class TestResolvePendingRequest:
    """Tests for ClaudeProcess.resolve_pending_request(request_id, response)."""

    def test_returns_true_and_resolves_active_future(self):
        """resolve_pending_request() returns True and sets the Future result for
        the matching request_id."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(process, req)

            response = PermissionResultAllow(updated_input={"command": "ls"})
            result = process.resolve_pending_request("req-A", response)

            assert result is True
            assert future.done()
            assert future.result() is response

        asyncio.run(run())

    def test_returns_false_when_no_pending_request(self):
        """resolve_pending_request() returns False when no Future is registered."""
        process = _make_claude_process()

        response = PermissionResultDeny(message="denied")
        result = process.resolve_pending_request("unknown-req", response)

        assert result is False

    def test_returns_false_for_unknown_request_id(self):
        """resolve_pending_request() returns False when request_id doesn't match."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request(request_id="req-A")
            _inject_pending(process, req)

            response = PermissionResultAllow(updated_input={})
            result = process.resolve_pending_request("req-B", response)

            assert result is False
            # The actual request is still pending
            assert "req-A" in process._pending_requests
            assert not process._pending_futures["req-A"].done()

        asyncio.run(run())

    def test_returns_false_when_future_already_resolved(self):
        """resolve_pending_request() returns False when the Future is already done."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(process, req)
            future.set_result(PermissionResultAllow(updated_input={}))

            response = PermissionResultDeny(message="too late")
            result = process.resolve_pending_request("req-A", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_future_already_cancelled(self):
        """resolve_pending_request() returns False when the Future is cancelled."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(process, req)
            future.cancel()

            response = PermissionResultAllow(updated_input={})
            result = process.resolve_pending_request("req-A", response)

            assert result is False

        asyncio.run(run())


# =============================================================================
# ClaudeProcess._cancel_pending_request_future()
# =============================================================================


class TestCancelPendingRequestFuture:
    """Tests for ClaudeProcess._cancel_pending_request_future() (cancel-all)."""

    def test_cancels_active_future_and_clears_state(self):
        """_cancel_pending_request_future() cancels the Future and clears both dicts."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(process, req)

            process._cancel_pending_request_future()

            assert future.cancelled()
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())

    def test_cancels_multiple_futures(self):
        """_cancel_pending_request_future() cancels every in-flight Future."""
        process = _make_claude_process()

        async def run():
            req1 = _make_pending_request(request_id="r1")
            req2 = _make_pending_request(request_id="r2")
            f1 = _inject_pending(process, req1)
            f2 = _inject_pending(process, req2)

            process._cancel_pending_request_future()

            assert f1.cancelled()
            assert f2.cancelled()
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())

    def test_handles_already_done_future(self):
        """_cancel_pending_request_future() does not raise when a Future is already done."""
        process = _make_claude_process()

        async def run():
            req = _make_pending_request()
            future = _inject_pending(process, req)
            future.set_result(PermissionResultAllow(updated_input={}))

            # Should not raise
            process._cancel_pending_request_future()

            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())

    def test_handles_no_futures(self):
        """_cancel_pending_request_future() handles empty state gracefully."""
        process = _make_claude_process()

        # Should not raise
        process._cancel_pending_request_future()

        assert process._pending_requests == {}
        assert process._pending_futures == {}


# =============================================================================
# kill / _handle_error cancel pending requests
# =============================================================================


class TestKillCancelsPendingRequest:
    """Tests that kill() cancels in-flight pending request Futures."""

    def test_kill_cancels_pending_future(self):
        """kill() cancels in-flight Futures so no asyncio warnings occur."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()
        process.state = ProcessState.ASSISTANT_TURN

        async def run():
            req = _make_pending_request()
            future = _inject_pending(process, req)

            await process.kill(reason="test")

            assert future.cancelled()
            assert process._pending_requests == {}
            assert process._pending_futures == {}
            assert process.state == ProcessState.DEAD
            assert process.kill_reason == "test"

        asyncio.run(run())

    def test_kill_without_pending_request_works(self):
        """kill() works correctly when there is no pending request."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()
        process.state = ProcessState.ASSISTANT_TURN

        async def run():
            await process.kill(reason="shutdown")

            assert process.state == ProcessState.DEAD
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())


class TestHandleErrorCancelsPendingRequest:
    """Tests that _handle_error() cancels in-flight pending request Futures."""

    def test_handle_error_cancels_pending_future(self):
        """_handle_error() cancels in-flight Futures so no asyncio warnings occur."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            req = _make_pending_request()
            future = _inject_pending(process, req)

            await process._handle_error("something broke")

            assert future.cancelled()
            assert process._pending_requests == {}
            assert process._pending_futures == {}
            assert process.state == ProcessState.DEAD
            assert process.error == "something broke"

        asyncio.run(run())

    def test_handle_error_without_pending_request_works(self):
        """_handle_error() works correctly when there is no pending request."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            await process._handle_error("some error")

            assert process.state == ProcessState.DEAD
            assert process.error == "some error"
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())


# =============================================================================
# ClaudeProcess.get_info() and pending_requests property
# =============================================================================


class TestGetInfoIncludesPendingRequests:
    """Tests that get_info() includes the pending requests in ProcessInfo."""

    def test_get_info_with_pending_requests(self):
        """get_info() includes the pending requests in the returned ProcessInfo."""
        process = _make_claude_process()
        process.state = ProcessState.DEAD  # Avoid memory query

        req = _make_pending_request()
        process._pending_requests[req.request_id] = req

        info = process.get_info()

        assert len(info.pending_requests) == 1
        assert info.pending_requests[0] is req

    def test_get_info_without_pending_requests(self):
        """get_info() returns an empty tuple for pending_requests when there are none."""
        process = _make_claude_process()
        process.state = ProcessState.DEAD

        info = process.get_info()

        assert info.pending_requests == ()


class TestPendingRequestsProperty:
    """Tests for the ClaudeProcess.pending_requests property."""

    def test_returns_empty_when_no_requests(self):
        """The pending_requests property returns an empty tuple by default."""
        process = _make_claude_process()
        assert process.pending_requests == ()

    def test_returns_requests_sorted_by_created_at(self):
        """The pending_requests property returns requests oldest-first."""
        process = _make_claude_process()

        # Insert in reverse chronological order to verify the sort
        req_newer = _make_pending_request(request_id="newer", created_at=2000.0)
        req_older = _make_pending_request(request_id="older", created_at=1000.0)
        process._pending_requests[req_newer.request_id] = req_newer
        process._pending_requests[req_older.request_id] = req_older

        result = process.pending_requests
        assert isinstance(result, tuple)
        assert [r.request_id for r in result] == ["older", "newer"]


# =============================================================================
# Pre-tool-use hook
# =============================================================================


class TestPreToolUseHook:
    """Tests for the module-level _pre_tool_use_hook function."""

    def test_returns_continue_true_for_non_edit_tools(self):
        """_pre_tool_use_hook() returns {'continue_': True} for tools that don't need capture."""
        from twicc.providers.claude_code.agent.process import _pre_tool_use_hook

        async def run():
            result = await _pre_tool_use_hook(
                {"tool_name": "Bash", "tool_input": {"command": "ls"}},
                "tool-use-123",
                None,
            )
            assert result == {"continue_": True}

        asyncio.run(run())


# =============================================================================
# ClaudeProcess._build_query_prompt (always async generator)
# =============================================================================


class TestBuildQueryPrompt:
    """Tests for ClaudeProcess._build_query_prompt() always returning an async generator."""

    def test_text_only_returns_async_generator(self):
        """_build_query_prompt() returns an async generator even for text-only messages."""
        process = _make_claude_process()

        async def run():
            result = process._build_query_prompt("hello", None, None)
            assert hasattr(result, "__aiter__")
            assert hasattr(result, "__anext__")

            messages = [msg async for msg in result]
            assert len(messages) == 1
            msg = messages[0]
            assert msg["type"] == "user"
            assert msg["message"]["role"] == "user"
            assert msg["parent_tool_use_id"] is None
            assert msg["message"]["content"] == [{"type": "text", "text": "hello"}]

        asyncio.run(run())

    def test_with_images(self):
        """_build_query_prompt() includes images before text in content blocks."""
        process = _make_claude_process()
        images = [{"type": "image", "source": {"data": "base64data"}}]

        async def run():
            result = process._build_query_prompt("describe this", images, None)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == images[0]
            assert content[1] == {"type": "text", "text": "describe this"}

        asyncio.run(run())

    def test_with_documents(self):
        """_build_query_prompt() includes documents before text in content blocks."""
        process = _make_claude_process()
        documents = [{"type": "document", "source": {"data": "pdfdata"}}]

        async def run():
            result = process._build_query_prompt("summarize", None, documents)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == documents[0]
            assert content[1] == {"type": "text", "text": "summarize"}

        asyncio.run(run())

    def test_with_images_and_documents(self):
        """_build_query_prompt() includes images first, then documents, then text."""
        process = _make_claude_process()
        images = [{"type": "image", "source": {"data": "img1"}}]
        documents = [{"type": "document", "source": {"data": "doc1"}}]

        async def run():
            result = process._build_query_prompt("analyze", images, documents)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 3
            assert content[0] == images[0]
            assert content[1] == documents[0]
            assert content[2] == {"type": "text", "text": "analyze"}

        asyncio.run(run())

    def test_empty_images_list_treated_as_no_images(self):
        """_build_query_prompt() with an empty images list only produces the text block."""
        process = _make_claude_process()

        async def run():
            result = process._build_query_prompt("hello", [], None)
            messages = [msg async for msg in result]
            content = messages[0]["message"]["content"]
            assert len(content) == 1
            assert content[0] == {"type": "text", "text": "hello"}

        asyncio.run(run())


# =============================================================================
# ProcessManager.resolve_pending_request(session_id, request_id, response)
# =============================================================================


class TestManagerResolvePendingRequest:
    """Tests for ProcessManager.resolve_pending_request()."""

    def test_routes_to_correct_process(self):
        """resolve_pending_request() finds the process and resolves the matching request."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, process, _ = _make_manager_with_process(
                pending_request=req, inject_future=True,
            )
            future = process._pending_futures["req-A"]

            response = PermissionResultAllow(updated_input={"command": "ls"})
            result = await manager.resolve_pending_request("session-1", "req-A", response)

            assert result is True
            assert future.done()
            assert future.result() is response

        asyncio.run(run())

    def test_routes_deny_response(self):
        """resolve_pending_request() correctly routes a deny response."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, process, _ = _make_manager_with_process(
                pending_request=req, inject_future=True,
            )
            future = process._pending_futures["req-A"]

            response = PermissionResultDeny(message="not allowed")
            result = await manager.resolve_pending_request("session-1", "req-A", response)

            assert result is True
            assert future.result() is response

        asyncio.run(run())

    def test_returns_false_for_unknown_session(self):
        """resolve_pending_request() returns False for a session_id not in _processes."""

        async def run():
            manager = ProcessManager()
            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("nonexistent", "req-X", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_process_has_no_pending_request(self):
        """resolve_pending_request() returns False when no Future matches the request_id."""

        async def run():
            manager, _process, _ = _make_manager_with_process()
            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("session-1", "req-X", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_for_unknown_request_id(self):
        """resolve_pending_request() returns False when request_id doesn't match any in-flight request."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, process, _ = _make_manager_with_process(
                pending_request=req, inject_future=True,
            )

            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("session-1", "req-B", response)

            assert result is False
            # The actual request is still pending
            assert "req-A" in process._pending_requests

        asyncio.run(run())

    def test_routes_to_correct_process_among_multiple(self):
        """resolve_pending_request() routes to the correct process when multiple exist."""

        async def run():
            manager = ProcessManager()

            process1 = _make_claude_process(session_id="session-1")
            process1.state = ProcessState.ASSISTANT_TURN
            process1._state_change_callback = AsyncMock()
            manager._processes["session-1"] = process1

            process2 = _make_claude_process(session_id="session-2")
            process2.state = ProcessState.ASSISTANT_TURN
            process2._state_change_callback = AsyncMock()
            req = _make_pending_request(request_id="req-2")
            future = _inject_pending(process2, req)
            manager._processes["session-2"] = process2

            response = PermissionResultAllow(updated_input={"command": "echo ok"})
            result = await manager.resolve_pending_request("session-2", "req-2", response)

            assert result is True
            assert future.done()
            assert future.result() is response
            # Process 1 unaffected
            assert process1._pending_requests == {}

        asyncio.run(run())


# =============================================================================
# ProcessManager.check_and_stop_timed_out_processes() with pending requests
# =============================================================================


class TestTimeoutExemptionForPendingRequest:
    """Tests that check_and_stop_timed_out_processes() skips processes with pending requests."""

    def test_process_with_pending_request_not_killed_in_assistant_turn(self):
        """A process in ASSISTANT_TURN with a pending request is not killed by timeout."""

        async def run():
            far_past = 1000.0
            manager, process, _ = _make_manager_with_process(
                state=ProcessState.ASSISTANT_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.ASSISTANT_TURN

        asyncio.run(run())

    def test_process_with_pending_request_not_killed_in_user_turn(self):
        """A process in USER_TURN with a pending request is not killed by timeout."""

        async def run():
            far_past = 1000.0
            manager, process, _ = _make_manager_with_process(
                state=ProcessState.USER_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.USER_TURN

        asyncio.run(run())

    def test_process_without_pending_request_is_killed_normally(self):
        """A process in ASSISTANT_TURN without a pending request is killed after timeout."""

        async def run():
            far_past = 1000.0
            manager, process, _ = _make_manager_with_process(
                state=ProcessState.ASSISTANT_TURN,
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == ["session-1"]
            assert process.state == ProcessState.DEAD

        asyncio.run(run())

    def test_mixed_processes_only_non_pending_killed(self):
        """Only processes without pending requests are killed; those with are spared."""

        async def run():
            far_past = 1000.0
            manager = ProcessManager()

            process1 = _make_claude_process(session_id="session-1")
            process1.state = ProcessState.ASSISTANT_TURN
            process1._state_change_callback = AsyncMock()
            process1._pending_requests[_make_pending_request().request_id] = _make_pending_request()
            process1.last_activity = far_past
            process1.state_changed_at = far_past
            manager._processes["session-1"] = process1

            process2 = _make_claude_process(session_id="session-2")
            process2.state = ProcessState.ASSISTANT_TURN
            process2._state_change_callback = AsyncMock()
            process2.last_activity = far_past
            process2.state_changed_at = far_past
            manager._processes["session-2"] = process2

            killed = await manager.check_and_stop_timed_out_processes()

            assert "session-2" in killed
            assert "session-1" not in killed
            assert process1.state == ProcessState.ASSISTANT_TURN
            assert process2.state == ProcessState.DEAD

        asyncio.run(run())

    def test_starting_process_with_pending_request_not_killed(self):
        """A process in STARTING state with a pending request is not killed."""

        async def run():
            far_past = 1000.0
            manager, process, _ = _make_manager_with_process(
                state=ProcessState.STARTING,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.STARTING

        asyncio.run(run())


# =============================================================================
# WebSocket handler _handle_pending_request_response
# =============================================================================


class _FakeConsumer:
    """Minimal stand-in exposing ``_handle_pending_request_response`` as a bound method.

    The real handler lives on ``ClaudeCodeWSHandler`` which expects a
    ``consumer`` instance. The tests don't exercise consumer-side calls,
    so we bind the unbound method directly onto the fake.
    """

    def __init__(self):
        from twicc.providers.claude_code.ws import ClaudeCodeWSHandler
        self._handle_pending_request_response = (
            ClaudeCodeWSHandler._handle_pending_request_response.__get__(self, type(self))
        )


class TestHandlePendingRequestResponseToolApproval:
    """Tests for _handle_pending_request_response with tool_approval request type."""

    def test_allow_resolves_pending_request(self):
        """An 'allow' decision resolves the matching Future with PermissionResultAllow."""

        async def run():
            req = _make_pending_request(
                request_id="req-A",
                tool_name="Bash",
                tool_input={"command": "echo hello"},
            )
            manager, process, _ = _make_manager_with_process(
                session_id="session-A",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-A"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-A",
                    "request_id": "req-A",
                    "request_type": "tool_approval",
                    "decision": "allow",
                    "updated_input": {"command": "echo hello"},
                })

            assert future.done()
            result = future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input == {"command": "echo hello"}

        asyncio.run(run())

    def test_allow_without_updated_input(self):
        """An 'allow' decision without updated_input passes None."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, process, _ = _make_manager_with_process(
                session_id="session-A",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-A"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-A",
                    "request_id": "req-A",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })

            result = future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input is None

        asyncio.run(run())

    def test_deny_resolves_with_permission_result_deny(self):
        """A 'deny' decision resolves the Future with PermissionResultDeny."""

        async def run():
            req = _make_pending_request(request_id="req-B")
            manager, process, _ = _make_manager_with_process(
                session_id="session-B",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-B"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-B",
                    "request_id": "req-B",
                    "request_type": "tool_approval",
                    "decision": "deny",
                    "message": "Too dangerous",
                })

            result = future.result()
            assert isinstance(result, PermissionResultDeny)
            assert result.message == "Too dangerous"

        asyncio.run(run())

    def test_deny_uses_default_message(self):
        """A 'deny' decision without a message uses the default reason."""

        async def run():
            req = _make_pending_request(request_id="req-B")
            manager, process, _ = _make_manager_with_process(
                session_id="session-B",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-B"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-B",
                    "request_id": "req-B",
                    "request_type": "tool_approval",
                    "decision": "deny",
                })

            result = future.result()
            assert isinstance(result, PermissionResultDeny)
            assert result.message == "User denied this action"

        asyncio.run(run())


class TestHandlePendingRequestResponseAskUserQuestion:
    """Tests for _handle_pending_request_response with ask_user_question request type."""

    def test_answers_resolve_with_original_questions(self):
        """ask_user_question responses include the original questions alongside answers."""
        questions = [
            {
                "question": "How should I format?",
                "header": "Format",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            }
        ]

        async def run():
            req = _make_pending_request(
                request_id="req-456",
                request_type="ask_user_question",
                tool_name="AskUserQuestion",
                tool_input={"questions": questions},
            )
            manager, process, _ = _make_manager_with_process(
                session_id="session-C",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-456"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-C",
                    "request_id": "req-456",
                    "request_type": "ask_user_question",
                    "answers": {"How should I format?": "JSON"},
                })

            result = future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input["questions"] == questions
            assert result.updated_input["answers"] == {"How should I format?": "JSON"}

        asyncio.run(run())

    def test_multiple_questions_and_answers(self):
        """Multiple questions map to multiple answers in the response."""
        questions = [
            {
                "question": "Output format?",
                "header": "Format",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            },
            {
                "question": "Include headers?",
                "header": "Headers",
                "options": [{"label": "Yes"}, {"label": "No"}],
                "multiSelect": False,
            },
        ]

        async def run():
            req = _make_pending_request(
                request_id="req-789",
                request_type="ask_user_question",
                tool_name="AskUserQuestion",
                tool_input={"questions": questions},
            )
            manager, process, _ = _make_manager_with_process(
                session_id="session-D",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-789"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-D",
                    "request_id": "req-789",
                    "request_type": "ask_user_question",
                    "answers": {
                        "Output format?": "CSV",
                        "Include headers?": "Yes",
                    },
                })

            result = future.result()
            assert result.updated_input["questions"] == questions
            assert result.updated_input["answers"] == {
                "Output format?": "CSV",
                "Include headers?": "Yes",
            }

        asyncio.run(run())

    def test_no_pending_request_does_not_resolve(self):
        """ask_user_question with no matching pending request on the process does nothing."""

        async def run():
            manager, process, _ = _make_manager_with_process(session_id="session-E")

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-E",
                    "request_id": "req-000",
                    "request_type": "ask_user_question",
                    "answers": {"question": "answer"},
                })

            # No futures registered on the process
            assert process._pending_futures == {}

        asyncio.run(run())


class TestHandlePendingRequestResponseEdgeCases:
    """Tests for edge cases in _handle_pending_request_response."""

    def test_missing_session_id_returns_early(self):
        """Missing session_id causes the handler to return early without errors."""

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager") as mock_manager:
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "request_id": "req-X",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })
                mock_manager.assert_not_called()

        asyncio.run(run())

    def test_missing_request_type_returns_early(self):
        """Missing request_type causes the handler to return early without errors."""

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager") as mock_manager:
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-X",
                    "request_id": "req-X",
                    "decision": "allow",
                })
                mock_manager.assert_not_called()

        asyncio.run(run())

    def test_missing_request_id_returns_early(self):
        """Missing request_id causes the handler to return early without errors.

        Without request_id we can't disambiguate between concurrent pending requests,
        so the handler must refuse.
        """

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager") as mock_manager:
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-X",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })
                mock_manager.assert_not_called()

        asyncio.run(run())

    def test_unknown_request_type_returns_early(self):
        """Unknown request_type causes the handler to return early."""

        async def run():
            manager = ProcessManager()
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-X",
                    "request_id": "req-X",
                    "request_type": "unknown_type",
                })

        asyncio.run(run())

    def test_unknown_session_does_not_raise(self):
        """Resolving for a non-existent session does not raise."""

        async def run():
            manager = ProcessManager()
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "nonexistent-session",
                    "request_id": "req-X",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })

        asyncio.run(run())

    def test_already_resolved_future_does_not_raise(self):
        """Sending a response when the matching Future is already resolved does not raise."""

        async def run():
            req = _make_pending_request(request_id="req-F")
            manager, process, _ = _make_manager_with_process(
                session_id="session-F",
                pending_request=req,
                inject_future=True,
            )
            future = process._pending_futures["req-F"]
            future.set_result(PermissionResultAllow(updated_input={}))

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-F",
                    "request_id": "req-F",
                    "request_type": "tool_approval",
                    "decision": "deny",
                    "message": "Too late",
                })

            # Original result is unchanged
            assert isinstance(future.result(), PermissionResultAllow)

        asyncio.run(run())


# =============================================================================
# Concurrent pending requests (regression tests for the parallel-tools bug)
# =============================================================================


class TestConcurrentPendingRequests:
    """Tests for the bug where parallel concurrency-safe tools (e.g. Read + Glob)
    each issue their own can_use_tool callback, and the second one used to overwrite
    the first one in the scalar slot — leaving its Future unresolved forever."""

    def test_two_concurrent_callbacks_do_not_overwrite_each_other(self):
        """Two parallel _handle_pending_request() calls register two distinct entries
        and resolve independently."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            task1 = asyncio.create_task(
                process._handle_pending_request(
                    "Read", {"file_path": "/x"}, _EMPTY_CONTEXT
                )
            )
            task2 = asyncio.create_task(
                process._handle_pending_request(
                    "Glob", {"pattern": "**/*.py"}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)  # let both register
            await asyncio.sleep(0)

            # Both requests are in flight at the same time
            assert len(process._pending_requests) == 2
            assert len(process._pending_futures) == 2
            tools = {r.tool_name for r in process._pending_requests.values()}
            assert tools == {"Read", "Glob"}

            # Resolve them in reverse order to confirm independence
            ids_by_tool = {r.tool_name: rid for rid, r in process._pending_requests.items()}
            process._pending_futures[ids_by_tool["Glob"]].set_result(
                PermissionResultAllow(updated_input={"pattern": "**/*.py"})
            )
            r2 = await task2

            # Read is still pending
            assert ids_by_tool["Read"] in process._pending_requests
            assert not process._pending_futures.get(ids_by_tool["Read"], asyncio.Future()).done() if ids_by_tool["Read"] in process._pending_futures else False

            process._pending_futures[ids_by_tool["Read"]].set_result(
                PermissionResultAllow(updated_input={"file_path": "/x"})
            )
            r1 = await task1

            assert isinstance(r1, PermissionResultAllow)
            assert isinstance(r2, PermissionResultAllow)
            assert r1.updated_input == {"file_path": "/x"}
            assert r2.updated_input == {"pattern": "**/*.py"}

            # Everything cleared
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())

    def test_resolve_picks_correct_request_among_concurrent(self):
        """resolve_pending_request() targets only the Future identified by request_id."""
        process = _make_claude_process()

        async def run():
            req1 = _make_pending_request(request_id="r1", tool_name="Read", created_at=1.0)
            req2 = _make_pending_request(request_id="r2", tool_name="Glob", created_at=2.0)
            f1 = _inject_pending(process, req1)
            f2 = _inject_pending(process, req2)

            response = PermissionResultAllow(updated_input={"pattern": "x"})
            assert process.resolve_pending_request("r2", response) is True

            # Only r2 is resolved; r1 stays pending
            assert f2.done()
            assert f2.result() is response
            assert not f1.done()

        asyncio.run(run())

    def test_pending_requests_property_orders_oldest_first(self):
        """The property exposes requests sorted by created_at ascending, regardless
        of the dict insertion order."""

        async def run():
            process = _make_claude_process()

            # Insert newest first
            new_req = _make_pending_request(request_id="new", created_at=2000.0)
            old_req = _make_pending_request(request_id="old", created_at=1000.0)
            _inject_pending(process, new_req)
            _inject_pending(process, old_req)

            ordered = process.pending_requests
            assert [r.request_id for r in ordered] == ["old", "new"]

        asyncio.run(run())

    def test_cancel_clears_all_concurrent_requests(self):
        """_cancel_pending_request_future() cancels every concurrent Future at once."""
        process = _make_claude_process()

        async def run():
            req1 = _make_pending_request(request_id="r1")
            req2 = _make_pending_request(request_id="r2")
            f1 = _inject_pending(process, req1)
            f2 = _inject_pending(process, req2)

            process._cancel_pending_request_future()

            assert f1.cancelled()
            assert f2.cancelled()
            assert process._pending_requests == {}
            assert process._pending_futures == {}

        asyncio.run(run())
