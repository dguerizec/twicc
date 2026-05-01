"""
Claude Code agent module.

Provides the infrastructure for running Claude Code SDK agents that enable
bidirectional communication with Claude Code from TwiCC.
"""

from .agent import ClaudeCodeAgent
from .manager import ClaudeCodeAgentManager, get_claude_code_agent_manager

__all__ = [
    "ClaudeCodeAgent",
    "ClaudeCodeAgentManager",
    "get_claude_code_agent_manager",
]
