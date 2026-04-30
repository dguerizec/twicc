"""
Provider-agnostic agent state types.

These types define the runtime snapshot that every agent provider exposes to
the rest of TwiCC (WebSocket layer, views, watchers). Provider-specific
extras (Claude permission suggestions, Codex tool approvals, ...) are folded
into the same fields when shape-compatible; the serializer omits empty fields.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

import psutil

from twicc.core.enums import Provider


def format_bytes(size: int) -> str:
    """Format a byte size into a human-readable string (e.g. ``"123.4 MB"``)."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_process_memory(pid: int) -> int | None:
    """Return RSS memory in bytes for ``pid``, or ``None`` if unavailable."""
    try:
        process = psutil.Process(pid)
        return process.memory_info().rss
    except Exception:
        # NoSuchProcess, AccessDenied, ZombieProcess, OSError, ...
        return None


@dataclass(frozen=True)
class PendingRequest:
    """A request from an agent that is waiting for user response.

    Covers tool approvals (the agent wants permission to run a tool) and
    clarifying questions (the agent needs the user to choose between options).
    Provider-specific metadata such as ``permission_suggestions`` stays
    optional so other providers can populate the common fields and ignore the
    rest.
    """

    request_id: str
    request_type: str
    tool_name: str
    tool_input: dict
    created_at: float
    permission_suggestions: list[dict] | None = None


class AgentState(StrEnum):
    """Lifecycle state of an agent.

    States:
        STARTING: The agent is initializing, before the first message is processed.
        ASSISTANT_TURN: The agent is working on a response.
        USER_TURN: The agent is waiting for user input (response complete).
        DEAD: The agent has terminated (error, kill, or shutdown).
    """

    STARTING = "starting"
    ASSISTANT_TURN = "assistant_turn"
    USER_TURN = "user_turn"
    DEAD = "dead"


class AgentInfo(NamedTuple):
    """Immutable snapshot of agent state for external consumption.

    The provider-specific fields (``pending_requests``, ``active_tools``,
    ``last_started_tool_id``) default to empty for providers that do not
    populate them; the serializer omits empty values.

    ``provider`` carries the provider key (e.g. ``Provider.CLAUDE_CODE``) so
    that multi-provider consumers can route or filter without re-querying the
    DB.
    """

    session_id: str
    project_id: str
    provider: Provider
    state: AgentState
    previous_state: AgentState | None
    started_at: float
    state_changed_at: float
    last_activity: float
    error: str | None = None
    memory_rss: int | None = None
    kill_reason: str | None = None
    pending_requests: tuple[PendingRequest, ...] = ()
    active_tools: tuple[dict, ...] = ()
    last_started_tool_id: str | None = None

    @property
    def memory_rss_human(self) -> str | None:
        """Human-readable memory usage, or ``None`` when ``memory_rss`` is missing."""
        if self.memory_rss is None:
            return None
        return format_bytes(self.memory_rss)


def serialize_agent_info(info: AgentInfo) -> dict:
    """Serialize an ``AgentInfo`` to a JSON-friendly dict."""
    data = {
        "session_id": info.session_id,
        "project_id": info.project_id,
        "provider": info.provider,
        "state": info.state,  # StrEnum serializes directly
        "started_at": info.started_at,
        "state_changed_at": info.state_changed_at,
    }
    if info.error is not None:
        data["error"] = info.error
    if info.memory_rss is not None:
        data["memory"] = info.memory_rss
    if info.kill_reason is not None:
        data["kill_reason"] = info.kill_reason
    if info.pending_requests:
        serialized = []
        for pr in info.pending_requests:
            entry = {
                "request_id": pr.request_id,
                "request_type": pr.request_type,
                "tool_name": pr.tool_name,
                "tool_input": pr.tool_input,
                "created_at": pr.created_at,
            }
            if pr.permission_suggestions:
                entry["permission_suggestions"] = pr.permission_suggestions
            serialized.append(entry)
        data["pending_requests"] = serialized
    if info.active_tools:
        data["active_tools"] = list(info.active_tools)
    if info.last_started_tool_id is not None:
        data["last_started_tool_id"] = info.last_started_tool_id
    return data
