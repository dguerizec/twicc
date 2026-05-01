"""
Tests for PendingRequest dataclass, AgentInfo serialization,
and ClaudeCodeAgent / ClaudeCodeAgentManager pending request mechanism.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from twicc.agent import (
    AgentInfo,
    AgentState,
    PendingRequest,
    serialize_agent_info,
)
from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings

# Mock context object with a suggestions attribute (mimics ToolPermissionContext)
_EMPTY_CONTEXT = SimpleNamespace(suggestions=[])


def _make_agent_info(**kwargs) -> AgentInfo:
    """Create an AgentInfo with sensible defaults, overridable via kwargs."""
    defaults = {
        "session_id": "test-session",
        "project_id": "test-project",
        "provider": Provider.CLAUDE_CODE,
        "state": AgentState.ASSISTANT_TURN,
        "previous_state": None,
        "started_at": 1000000.0,
        "state_changed_at": 1000001.0,
        "last_activity": 1000002.0,
    }
    defaults.update(kwargs)
    return AgentInfo(**defaults)


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


def _make_claude_agent(session_id: str = "test-session-1") -> ClaudeCodeAgent:
    """Create a ClaudeCodeAgent for testing, without starting it."""
    return ClaudeCodeAgent(
        session_id=session_id,
        project_id="test-project-1",
        cwd="/tmp/test",
        settings=AgentSettings(permission_mode="default"),
        get_session_slug=_dummy_get_slug,
        on_cron_created=_dummy_on_cron_created,
        on_cron_deleted=_dummy_on_cron_deleted,
    )


def _inject_pending(agent: ClaudeCodeAgent, request: PendingRequest, future: asyncio.Future | None = None) -> asyncio.Future:
    """Directly inject a pending request + Future on an agent (test helper)."""
    if future is None:
        future = asyncio.get_event_loop().create_future()
    agent._pending_requests[request.request_id] = request
    agent._pending_futures[request.request_id] = future
    return future


def _make_manager_with_agent(
    session_id: str = "session-1",
    state: AgentState = AgentState.ASSISTANT_TURN,
    pending_request: PendingRequest | None = None,
    last_activity: float | None = None,
    state_changed_at: float | None = None,
    inject_future: bool = False,
) -> tuple[ClaudeCodeAgentManager, ClaudeCodeAgent, asyncio.Future | None]:
    """Create a ClaudeCodeAgentManager with a single mock agent injected directly.

    Returns (manager, agent, future). The future is non-None when a pending
    request was injected.
    """
    manager = ClaudeCodeAgentManager()
    agent = _make_claude_agent(session_id=session_id)
    agent.state = state
    agent._state_change_callback = AsyncMock()
    future: asyncio.Future | None = None
    if pending_request is not None:
        future = _inject_pending(agent, pending_request) if inject_future else None
        agent._pending_requests[pending_request.request_id] = pending_request
        if inject_future and future is None:
            future = asyncio.get_event_loop().create_future()
            agent._pending_futures[pending_request.request_id] = future
    if last_activity is not None:
        agent.last_activity = last_activity
    if state_changed_at is not None:
        agent.state_changed_at = state_changed_at
    manager._agents[session_id] = agent
    return manager, agent, future


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
# AgentInfo with pending_requests
# =============================================================================


class TestAgentInfoWithPendingRequests:
    """Tests for PendingRequest integration in AgentInfo."""

    def test_pending_requests_defaults_to_empty_tuple(self):
        """AgentInfo.pending_requests defaults to an empty tuple."""
        info = _make_agent_info()
        assert info.pending_requests == ()

    def test_pending_requests_can_hold_multiple(self):
        """AgentInfo can hold multiple pending requests."""
        req1 = _make_pending_request(request_id="r1", tool_name="Read")
        req2 = _make_pending_request(request_id="r2", tool_name="Glob")
        info = _make_agent_info(pending_requests=(req1, req2))
        assert len(info.pending_requests) == 2
        assert info.pending_requests[0].request_id == "r1"
        assert info.pending_requests[1].request_id == "r2"


# =============================================================================
# serialize_agent_info() with pending_requests
# =============================================================================


class TestSerializeAgentInfoPendingRequests:
    """Tests for pending_requests serialization in serialize_agent_info()."""

    def test_no_pending_requests_omits_key(self):
        """When pending_requests is empty, the serialized dict has no 'pending_requests' key."""
        info = _make_agent_info()
        data = serialize_agent_info(info)
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
        info = _make_agent_info(pending_requests=(req,))
        data = serialize_agent_info(info)

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
        info = _make_agent_info(pending_requests=(req1, req2))
        data = serialize_agent_info(info)

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
        info = _make_agent_info(pending_requests=(req,))
        data = serialize_agent_info(info)

        pr = data["pending_requests"][0]
        assert pr["request_type"] == "ask_user_question"
        assert pr["tool_name"] == "AskUserQuestion"
        assert pr["tool_input"]["questions"] == questions

    def test_serialized_pending_request_has_exactly_five_keys(self):
        """The serialized pending request dict contains exactly the five expected keys."""
        req = _make_pending_request()
        info = _make_agent_info(pending_requests=(req,))
        data = serialize_agent_info(info)

        pr = data["pending_requests"][0]
        assert set(pr.keys()) == {"request_id", "request_type", "tool_name", "tool_input", "created_at"}

    def test_permission_suggestions_included_when_present(self):
        """The optional permission_suggestions key is included when set."""
        suggestions = [{"type": "addRules", "rules": [{"toolName": "Read", "ruleContent": "/x/**"}], "behavior": "allow"}]
        req = _make_pending_request(permission_suggestions=suggestions)
        info = _make_agent_info(pending_requests=(req,))
        data = serialize_agent_info(info)
        assert data["pending_requests"][0]["permission_suggestions"] == suggestions

    def test_other_fields_unaffected_by_pending_requests(self):
        """Adding pending_requests does not change serialization of other fields."""
        info_without = _make_agent_info(error="some error", kill_reason="manual")
        info_with = _make_agent_info(
            error="some error",
            kill_reason="manual",
            pending_requests=(_make_pending_request(),),
        )

        data_without = serialize_agent_info(info_without)
        data_with = serialize_agent_info(info_with)

        for key in data_without:
            assert data_with[key] == data_without[key]

        assert set(data_with.keys()) - set(data_without.keys()) == {"pending_requests"}


# =============================================================================
# ClaudeCodeAgent._handle_pending_request()
# =============================================================================


class TestHandlePendingRequest:
    """Tests for ClaudeCodeAgent._handle_pending_request()."""

    def test_creates_pending_request_and_blocks_on_future(self):
        """_handle_pending_request() registers a request, notifies state change,
        then blocks on its Future. After resolution, the request is removed and
        a second notification fires."""
        agent = _make_claude_agent()
        state_change_calls = []

        async def mock_state_change(proc):
            state_change_calls.append(tuple(proc.pending_requests))

        agent._state_change_callback = mock_state_change

        async def run():
            task = asyncio.create_task(
                agent._handle_pending_request(
                    "Bash", {"command": "ls"}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)

            # Exactly one in-flight request, with the expected fields
            assert len(agent._pending_requests) == 1
            request_id, req = next(iter(agent._pending_requests.items()))
            assert req.request_type == "tool_approval"
            assert req.tool_name == "Bash"
            assert req.tool_input == {"command": "ls"}
            assert req.request_id == request_id  # request_id field matches the dict key

            # Future exists and is unresolved
            future = agent._pending_futures[request_id]
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
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

            # Second notification fired with no requests
            assert len(state_change_calls) == 2
            assert state_change_calls[1] == ()

            assert result is response

        asyncio.run(run())

    def test_ask_user_question_sets_correct_type(self):
        """_handle_pending_request() sets request_type to 'ask_user_question'
        when tool_name is 'AskUserQuestion'."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()

        async def run():
            questions = [{"question": "Which format?", "options": [{"label": "JSON"}]}]
            task = asyncio.create_task(
                agent._handle_pending_request(
                    "AskUserQuestion", {"questions": questions}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)

            assert len(agent._pending_requests) == 1
            req = next(iter(agent._pending_requests.values()))
            assert req.request_type == "ask_user_question"
            assert req.tool_name == "AskUserQuestion"

            # Resolve to clean up
            future = next(iter(agent._pending_futures.values()))
            future.set_result(PermissionResultAllow(updated_input={"questions": questions}))
            await task

        asyncio.run(run())

    def test_non_ask_user_question_tools_are_tool_approval(self):
        """_handle_pending_request() sets request_type to 'tool_approval'
        for any tool other than 'AskUserQuestion'."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()

        async def run():
            for tool_name in ("Bash", "Write", "Edit", "Read"):
                task = asyncio.create_task(
                    agent._handle_pending_request(
                        tool_name, {"file_path": "/test"}, _EMPTY_CONTEXT
                    )
                )
                await asyncio.sleep(0)

                req = next(iter(agent._pending_requests.values()))
                assert req.request_type == "tool_approval"
                assert req.tool_name == tool_name

                future = next(iter(agent._pending_futures.values()))
                future.set_result(PermissionResultAllow(updated_input={}))
                await task

        asyncio.run(run())


# =============================================================================
# ClaudeCodeAgent.resolve_pending_request()
# =============================================================================


class TestResolvePendingRequest:
    """Tests for ClaudeCodeAgent.resolve_pending_request(request_id, response)."""

    def test_returns_true_and_resolves_active_future(self):
        """resolve_pending_request() returns True and sets the Future result for
        the matching request_id."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(agent, req)

            response = PermissionResultAllow(updated_input={"command": "ls"})
            result = agent.resolve_pending_request("req-A", response)

            assert result is True
            assert future.done()
            assert future.result() is response

        asyncio.run(run())

    def test_returns_false_when_no_pending_request(self):
        """resolve_pending_request() returns False when no Future is registered."""
        agent = _make_claude_agent()

        response = PermissionResultDeny(message="denied")
        result = agent.resolve_pending_request("unknown-req", response)

        assert result is False

    def test_returns_false_for_unknown_request_id(self):
        """resolve_pending_request() returns False when request_id doesn't match."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request(request_id="req-A")
            _inject_pending(agent, req)

            response = PermissionResultAllow(updated_input={})
            result = agent.resolve_pending_request("req-B", response)

            assert result is False
            # The actual request is still pending
            assert "req-A" in agent._pending_requests
            assert not agent._pending_futures["req-A"].done()

        asyncio.run(run())

    def test_returns_false_when_future_already_resolved(self):
        """resolve_pending_request() returns False when the Future is already done."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(agent, req)
            future.set_result(PermissionResultAllow(updated_input={}))

            response = PermissionResultDeny(message="too late")
            result = agent.resolve_pending_request("req-A", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_future_already_cancelled(self):
        """resolve_pending_request() returns False when the Future is cancelled."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(agent, req)
            future.cancel()

            response = PermissionResultAllow(updated_input={})
            result = agent.resolve_pending_request("req-A", response)

            assert result is False

        asyncio.run(run())


# =============================================================================
# ClaudeCodeAgent._cancel_pending_request_future()
# =============================================================================


class TestCancelPendingRequestFuture:
    """Tests for ClaudeCodeAgent._cancel_pending_request_future() (cancel-all)."""

    def test_cancels_active_future_and_clears_state(self):
        """_cancel_pending_request_future() cancels the Future and clears both dicts."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request(request_id="req-A")
            future = _inject_pending(agent, req)

            agent._cancel_pending_request_future()

            assert future.cancelled()
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())

    def test_cancels_multiple_futures(self):
        """_cancel_pending_request_future() cancels every in-flight Future."""
        agent = _make_claude_agent()

        async def run():
            req1 = _make_pending_request(request_id="r1")
            req2 = _make_pending_request(request_id="r2")
            f1 = _inject_pending(agent, req1)
            f2 = _inject_pending(agent, req2)

            agent._cancel_pending_request_future()

            assert f1.cancelled()
            assert f2.cancelled()
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())

    def test_handles_already_done_future(self):
        """_cancel_pending_request_future() does not raise when a Future is already done."""
        agent = _make_claude_agent()

        async def run():
            req = _make_pending_request()
            future = _inject_pending(agent, req)
            future.set_result(PermissionResultAllow(updated_input={}))

            # Should not raise
            agent._cancel_pending_request_future()

            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())

    def test_handles_no_futures(self):
        """_cancel_pending_request_future() handles empty state gracefully."""
        agent = _make_claude_agent()

        # Should not raise
        agent._cancel_pending_request_future()

        assert agent._pending_requests == {}
        assert agent._pending_futures == {}


# =============================================================================
# kill / _handle_error cancel pending requests
# =============================================================================


class TestKillCancelsPendingRequest:
    """Tests that kill() cancels in-flight pending request Futures."""

    def test_kill_cancels_pending_future(self):
        """kill() cancels in-flight Futures so no asyncio warnings occur."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()
        agent.state = AgentState.ASSISTANT_TURN

        async def run():
            req = _make_pending_request()
            future = _inject_pending(agent, req)

            await agent.kill(reason="test")

            assert future.cancelled()
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}
            assert agent.state == AgentState.DEAD
            assert agent.kill_reason == "test"

        asyncio.run(run())

    def test_kill_without_pending_request_works(self):
        """kill() works correctly when there is no pending request."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()
        agent.state = AgentState.ASSISTANT_TURN

        async def run():
            await agent.kill(reason="shutdown")

            assert agent.state == AgentState.DEAD
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())


class TestHandleErrorCancelsPendingRequest:
    """Tests that _handle_error() cancels in-flight pending request Futures."""

    def test_handle_error_cancels_pending_future(self):
        """_handle_error() cancels in-flight Futures so no asyncio warnings occur."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()

        async def run():
            req = _make_pending_request()
            future = _inject_pending(agent, req)

            await agent._handle_error("something broke")

            assert future.cancelled()
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}
            assert agent.state == AgentState.DEAD
            assert agent.error == "something broke"

        asyncio.run(run())

    def test_handle_error_without_pending_request_works(self):
        """_handle_error() works correctly when there is no pending request."""
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()

        async def run():
            await agent._handle_error("some error")

            assert agent.state == AgentState.DEAD
            assert agent.error == "some error"
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())


# =============================================================================
# ClaudeCodeAgent.get_info() and pending_requests property
# =============================================================================


class TestGetInfoIncludesPendingRequests:
    """Tests that get_info() includes the pending requests in AgentInfo."""

    def test_get_info_with_pending_requests(self):
        """get_info() includes the pending requests in the returned AgentInfo."""
        agent = _make_claude_agent()
        agent.state = AgentState.DEAD  # Avoid memory query

        req = _make_pending_request()
        agent._pending_requests[req.request_id] = req

        info = agent.get_info()

        assert len(info.pending_requests) == 1
        assert info.pending_requests[0] is req

    def test_get_info_without_pending_requests(self):
        """get_info() returns an empty tuple for pending_requests when there are none."""
        agent = _make_claude_agent()
        agent.state = AgentState.DEAD

        info = agent.get_info()

        assert info.pending_requests == ()


class TestPendingRequestsProperty:
    """Tests for the ClaudeCodeAgent.pending_requests property."""

    def test_returns_empty_when_no_requests(self):
        """The pending_requests property returns an empty tuple by default."""
        agent = _make_claude_agent()
        assert agent.pending_requests == ()

    def test_returns_requests_sorted_by_created_at(self):
        """The pending_requests property returns requests oldest-first."""
        agent = _make_claude_agent()

        # Insert in reverse chronological order to verify the sort
        req_newer = _make_pending_request(request_id="newer", created_at=2000.0)
        req_older = _make_pending_request(request_id="older", created_at=1000.0)
        agent._pending_requests[req_newer.request_id] = req_newer
        agent._pending_requests[req_older.request_id] = req_older

        result = agent.pending_requests
        assert isinstance(result, tuple)
        assert [r.request_id for r in result] == ["older", "newer"]


# =============================================================================
# ClaudeCodeAgent._build_query_prompt (always async generator)
# =============================================================================


class TestBuildQueryPrompt:
    """Tests for ClaudeCodeAgent._build_query_prompt() always returning an async generator."""

    def test_text_only_returns_async_generator(self):
        """_build_query_prompt() returns an async generator even for text-only messages."""
        agent = _make_claude_agent()

        async def run():
            result = agent._build_query_prompt("hello", None, None)
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
        agent = _make_claude_agent()
        images = [{"type": "image", "source": {"data": "base64data"}}]

        async def run():
            result = agent._build_query_prompt("describe this", images, None)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == images[0]
            assert content[1] == {"type": "text", "text": "describe this"}

        asyncio.run(run())

    def test_with_documents(self):
        """_build_query_prompt() includes documents before text in content blocks."""
        agent = _make_claude_agent()
        documents = [{"type": "document", "source": {"data": "pdfdata"}}]

        async def run():
            result = agent._build_query_prompt("summarize", None, documents)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == documents[0]
            assert content[1] == {"type": "text", "text": "summarize"}

        asyncio.run(run())

    def test_with_images_and_documents(self):
        """_build_query_prompt() includes images first, then documents, then text."""
        agent = _make_claude_agent()
        images = [{"type": "image", "source": {"data": "img1"}}]
        documents = [{"type": "document", "source": {"data": "doc1"}}]

        async def run():
            result = agent._build_query_prompt("analyze", images, documents)
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
        agent = _make_claude_agent()

        async def run():
            result = agent._build_query_prompt("hello", [], None)
            messages = [msg async for msg in result]
            content = messages[0]["message"]["content"]
            assert len(content) == 1
            assert content[0] == {"type": "text", "text": "hello"}

        asyncio.run(run())


# =============================================================================
# ClaudeCodeAgentManager.resolve_pending_request(session_id, request_id, response)
# =============================================================================


class TestManagerResolvePendingRequest:
    """Tests for ClaudeCodeAgentManager.resolve_pending_request()."""

    def test_routes_to_correct_agent(self):
        """resolve_pending_request() finds the agent and resolves the matching request."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, agent, _ = _make_manager_with_agent(
                pending_request=req, inject_future=True,
            )
            future = agent._pending_futures["req-A"]

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
            manager, agent, _ = _make_manager_with_agent(
                pending_request=req, inject_future=True,
            )
            future = agent._pending_futures["req-A"]

            response = PermissionResultDeny(message="not allowed")
            result = await manager.resolve_pending_request("session-1", "req-A", response)

            assert result is True
            assert future.result() is response

        asyncio.run(run())

    def test_returns_false_for_unknown_session(self):
        """resolve_pending_request() returns False for a session_id not in _agents."""

        async def run():
            manager = ClaudeCodeAgentManager()
            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("nonexistent", "req-X", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_agent_has_no_pending_request(self):
        """resolve_pending_request() returns False when no Future matches the request_id."""

        async def run():
            manager, _agent, _ = _make_manager_with_agent()
            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("session-1", "req-X", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_for_unknown_request_id(self):
        """resolve_pending_request() returns False when request_id doesn't match any in-flight request."""

        async def run():
            req = _make_pending_request(request_id="req-A")
            manager, agent, _ = _make_manager_with_agent(
                pending_request=req, inject_future=True,
            )

            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("session-1", "req-B", response)

            assert result is False
            # The actual request is still pending
            assert "req-A" in agent._pending_requests

        asyncio.run(run())

    def test_routes_to_correct_agent_among_multiple(self):
        """resolve_pending_request() routes to the correct agent when multiple exist."""

        async def run():
            manager = ClaudeCodeAgentManager()

            agent1 = _make_claude_agent(session_id="session-1")
            agent1.state = AgentState.ASSISTANT_TURN
            agent1._state_change_callback = AsyncMock()
            manager._agents["session-1"] = agent1

            agent2 = _make_claude_agent(session_id="session-2")
            agent2.state = AgentState.ASSISTANT_TURN
            agent2._state_change_callback = AsyncMock()
            req = _make_pending_request(request_id="req-2")
            future = _inject_pending(agent2, req)
            manager._agents["session-2"] = agent2

            response = PermissionResultAllow(updated_input={"command": "echo ok"})
            result = await manager.resolve_pending_request("session-2", "req-2", response)

            assert result is True
            assert future.done()
            assert future.result() is response
            # Agent 1 unaffected
            assert agent1._pending_requests == {}

        asyncio.run(run())


# =============================================================================
# ClaudeCodeAgentManager.check_and_stop_timed_out_agents() with pending requests
# =============================================================================


class TestTimeoutExemptionForPendingRequest:
    """Tests that check_and_stop_timed_out_agents() skips agents with pending requests.

    Each test patches SessionCron.has_active_for_session to avoid DB access — the
    cron-skipping behavior is independent from the pending-request-skipping we test.
    """

    def test_agent_with_pending_request_not_killed_in_assistant_turn(self):
        """An agent in ASSISTANT_TURN with a pending request is not killed by timeout."""

        async def run():
            far_past = 1000.0
            manager, agent, _ = _make_manager_with_agent(
                state=AgentState.ASSISTANT_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            with patch("twicc.core.models.SessionCron.has_active_for_session", return_value=False):
                killed = await manager.check_and_stop_timed_out_agents()

            assert killed == []
            assert agent.state == AgentState.ASSISTANT_TURN

        asyncio.run(run())

    def test_agent_with_pending_request_not_killed_in_user_turn(self):
        """An agent in USER_TURN with a pending request is not killed by timeout."""

        async def run():
            far_past = 1000.0
            manager, agent, _ = _make_manager_with_agent(
                state=AgentState.USER_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            with patch("twicc.core.models.SessionCron.has_active_for_session", return_value=False):
                killed = await manager.check_and_stop_timed_out_agents()

            assert killed == []
            assert agent.state == AgentState.USER_TURN

        asyncio.run(run())

    def test_agent_without_pending_request_is_killed_normally(self):
        """An agent in ASSISTANT_TURN without a pending request is killed after timeout."""

        async def run():
            far_past = 1000.0
            manager, agent, _ = _make_manager_with_agent(
                state=AgentState.ASSISTANT_TURN,
                last_activity=far_past,
                state_changed_at=far_past,
            )

            with patch("twicc.core.models.SessionCron.has_active_for_session", return_value=False):
                killed = await manager.check_and_stop_timed_out_agents()

            assert killed == ["session-1"]
            assert agent.state == AgentState.DEAD

        asyncio.run(run())

    def test_mixed_agents_only_non_pending_killed(self):
        """Only agents without pending requests are killed; those with are spared."""

        async def run():
            far_past = 1000.0
            manager = ClaudeCodeAgentManager()

            agent1 = _make_claude_agent(session_id="session-1")
            agent1.state = AgentState.ASSISTANT_TURN
            agent1._state_change_callback = AsyncMock()
            agent1._pending_requests[_make_pending_request().request_id] = _make_pending_request()
            agent1.last_activity = far_past
            agent1.state_changed_at = far_past
            manager._agents["session-1"] = agent1

            agent2 = _make_claude_agent(session_id="session-2")
            agent2.state = AgentState.ASSISTANT_TURN
            agent2._state_change_callback = AsyncMock()
            agent2.last_activity = far_past
            agent2.state_changed_at = far_past
            manager._agents["session-2"] = agent2

            with patch("twicc.core.models.SessionCron.has_active_for_session", return_value=False):
                killed = await manager.check_and_stop_timed_out_agents()

            assert "session-2" in killed
            assert "session-1" not in killed
            assert agent1.state == AgentState.ASSISTANT_TURN
            assert agent2.state == AgentState.DEAD

        asyncio.run(run())

    def test_starting_agent_with_pending_request_not_killed(self):
        """An agent in STARTING state with a pending request is not killed."""

        async def run():
            far_past = 1000.0
            manager, agent, _ = _make_manager_with_agent(
                state=AgentState.STARTING,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            with patch("twicc.core.models.SessionCron.has_active_for_session", return_value=False):
                killed = await manager.check_and_stop_timed_out_agents()

            assert killed == []
            assert agent.state == AgentState.STARTING

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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-A",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-A"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-A",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-A"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-B",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-B"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-B",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-B"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-C",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-456"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-D",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-789"]

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
        """ask_user_question with no matching pending request on the agent does nothing."""

        async def run():
            manager, agent, _ = _make_manager_with_agent(session_id="session-E")

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-E",
                    "request_id": "req-000",
                    "request_type": "ask_user_question",
                    "answers": {"question": "answer"},
                })

            # No futures registered on the agent
            assert agent._pending_futures == {}

        asyncio.run(run())


class TestHandlePendingRequestResponseEdgeCases:
    """Tests for edge cases in _handle_pending_request_response."""

    def test_missing_session_id_returns_early(self):
        """Missing session_id causes the handler to return early without errors."""

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager") as mock_manager:
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

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager") as mock_manager:
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

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager") as mock_manager:
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
            manager = ClaudeCodeAgentManager()
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager = ClaudeCodeAgentManager()
            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
            manager, agent, _ = _make_manager_with_agent(
                session_id="session-F",
                pending_request=req,
                inject_future=True,
            )
            future = agent._pending_futures["req-F"]
            future.set_result(PermissionResultAllow(updated_input={}))

            consumer = _FakeConsumer()

            with patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager", return_value=manager):
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
        agent = _make_claude_agent()
        agent._state_change_callback = AsyncMock()

        async def run():
            task1 = asyncio.create_task(
                agent._handle_pending_request(
                    "Read", {"file_path": "/x"}, _EMPTY_CONTEXT
                )
            )
            task2 = asyncio.create_task(
                agent._handle_pending_request(
                    "Glob", {"pattern": "**/*.py"}, _EMPTY_CONTEXT
                )
            )
            await asyncio.sleep(0)  # let both register
            await asyncio.sleep(0)

            # Both requests are in flight at the same time
            assert len(agent._pending_requests) == 2
            assert len(agent._pending_futures) == 2
            tools = {r.tool_name for r in agent._pending_requests.values()}
            assert tools == {"Read", "Glob"}

            # Resolve them in reverse order to confirm independence
            ids_by_tool = {r.tool_name: rid for rid, r in agent._pending_requests.items()}
            agent._pending_futures[ids_by_tool["Glob"]].set_result(
                PermissionResultAllow(updated_input={"pattern": "**/*.py"})
            )
            r2 = await task2

            # Read is still pending
            assert ids_by_tool["Read"] in agent._pending_requests
            assert not agent._pending_futures.get(ids_by_tool["Read"], asyncio.Future()).done() if ids_by_tool["Read"] in agent._pending_futures else False

            agent._pending_futures[ids_by_tool["Read"]].set_result(
                PermissionResultAllow(updated_input={"file_path": "/x"})
            )
            r1 = await task1

            assert isinstance(r1, PermissionResultAllow)
            assert isinstance(r2, PermissionResultAllow)
            assert r1.updated_input == {"file_path": "/x"}
            assert r2.updated_input == {"pattern": "**/*.py"}

            # Everything cleared
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())

    def test_resolve_picks_correct_request_among_concurrent(self):
        """resolve_pending_request() targets only the Future identified by request_id."""
        agent = _make_claude_agent()

        async def run():
            req1 = _make_pending_request(request_id="r1", tool_name="Read", created_at=1.0)
            req2 = _make_pending_request(request_id="r2", tool_name="Glob", created_at=2.0)
            f1 = _inject_pending(agent, req1)
            f2 = _inject_pending(agent, req2)

            response = PermissionResultAllow(updated_input={"pattern": "x"})
            assert agent.resolve_pending_request("r2", response) is True

            # Only r2 is resolved; r1 stays pending
            assert f2.done()
            assert f2.result() is response
            assert not f1.done()

        asyncio.run(run())

    def test_pending_requests_property_orders_oldest_first(self):
        """The property exposes requests sorted by created_at ascending, regardless
        of the dict insertion order."""

        async def run():
            agent = _make_claude_agent()

            # Insert newest first
            new_req = _make_pending_request(request_id="new", created_at=2000.0)
            old_req = _make_pending_request(request_id="old", created_at=1000.0)
            _inject_pending(agent, new_req)
            _inject_pending(agent, old_req)

            ordered = agent.pending_requests
            assert [r.request_id for r in ordered] == ["old", "new"]

        asyncio.run(run())

    def test_cancel_clears_all_concurrent_requests(self):
        """_cancel_pending_request_future() cancels every concurrent Future at once."""
        agent = _make_claude_agent()

        async def run():
            req1 = _make_pending_request(request_id="r1")
            req2 = _make_pending_request(request_id="r2")
            f1 = _inject_pending(agent, req1)
            f2 = _inject_pending(agent, req2)

            agent._cancel_pending_request_future()

            assert f1.cancelled()
            assert f2.cancelled()
            assert agent._pending_requests == {}
            assert agent._pending_futures == {}

        asyncio.run(run())
