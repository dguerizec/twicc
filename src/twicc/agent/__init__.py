"""
Provider-agnostic agent runtime.

A TwiCC "agent" is one running instance of an external coding-agent runtime
(Claude Code SDK, Codex CLI, Gemini CLI, ...) bound to a single TwiCC session.
This package exposes the base classes and shared types that every provider
implementation extends; provider-specific code lives in
``twicc.providers.<name>.agent``.

The cross-provider registry (``AgentManagerRegistry``) lives in
``twicc.agent.registry`` and is *not* re-exported here on purpose: provider
managers themselves import this package, so loading the registry from
``__init__`` would create an import cycle. Consumers that need the registry
should ``from twicc.agent.registry import get_agent_manager_registry``.
"""

from .base_agent import BaseAgent, StateChangeCallback
from .base_manager import BaseAgentManager, BroadcastCallback
from .exceptions import SendDeliveryError
from .states import (
    AgentInfo,
    AgentState,
    PendingRequest,
    format_bytes,
    get_process_memory,
    serialize_agent_info,
)

__all__ = [
    "AgentInfo",
    "AgentState",
    "BaseAgent",
    "BaseAgentManager",
    "BroadcastCallback",
    "PendingRequest",
    "SendDeliveryError",
    "StateChangeCallback",
    "format_bytes",
    "get_process_memory",
    "serialize_agent_info",
]
