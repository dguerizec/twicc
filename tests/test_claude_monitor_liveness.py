"""Runtime liveness tracking for Claude Code ``Monitor`` tools."""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import UserMessage

from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent


def _agent() -> ClaudeCodeAgent:
    """Build the small in-memory slice used by monitor tracking."""
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "session-1"
    agent._live_monitor_tasks = set()
    agent._live_background_tasks = {}
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._broadcast_process_label = AsyncMock()
    return agent


def test_monitor_start_holds_liveness_until_terminal_notification() -> None:
    agent = _agent()

    asyncio.run(agent._update_live_monitor_tasks(UserMessage(
        content="Monitor started (task monitor-1, timeout 600000ms).",
        tool_use_result={"taskId": "monitor-1", "timeoutMs": 600000, "persistent": False},
    )))

    assert agent._live_monitor_tasks == {"monitor-1"}

    # Progress events must not end the monitor.
    asyncio.run(agent._update_live_monitor_tasks(UserMessage(
        content="<task-notification>\n<task-id>monitor-1</task-id>\n"
        "<event>still running</event>\n</task-notification>",
    )))
    assert agent._live_monitor_tasks == {"monitor-1"}

    asyncio.run(agent._update_live_monitor_tasks(UserMessage(
        content="<task-notification>\n<task-id>monitor-1</task-id>\n"
        "<status>completed</status>\n</task-notification>",
    )))
    assert agent._live_monitor_tasks == set()


def test_task_stop_result_ends_monitor_from_its_json_text_payload() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._update_live_monitor_tasks(UserMessage(
        content=(
            '{"message":"Successfully stopped task: monitor-1",'
            '"task_id":"monitor-1","task_type":"local_bash"}'
        ),
    )))

    assert agent._live_monitor_tasks == set()


def test_monitor_label_is_used_while_no_higher_priority_wait_exists() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._refresh_waiting_label())

    agent._broadcast_process_label.assert_awaited_once_with("monitoring")
    assert agent._waiting_label_active is True


def test_background_subagent_label_takes_priority_over_monitoring() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")
    agent._live_background_tasks["agent-1"] = "review"

    asyncio.run(agent._refresh_waiting_label())

    agent._broadcast_process_label.assert_awaited_once_with("waiting for 1 subagent")
