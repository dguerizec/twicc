"""
Claude Code agent module.

Provides the infrastructure for running Claude Code SDK agents that enable
bidirectional communication with Claude Code from TwiCC.
"""

from .agent import ClaudeAgent
from .manager import ClaudeAgentManager, get_claude_agent_manager

__all__ = [
    "ClaudeAgent",
    "ClaudeAgentManager",
    "get_claude_agent_manager",
]
