"""JSONL-derived state signals for hybrid sessions.

Produced by the Claude Code sessions watcher (one per ingest batch with
fresh lines, hybrid sessions only) and consumed by
``ClaudeCodeAgentManager.handle_hybrid_jsonl_signals``.
"""

from typing import NamedTuple


class HybridJsonlSignals(NamedTuple):
    # A real user prompt landed (computed ItemKind.USER_MESSAGE — excludes
    # meta lines, tool results and system XML) → the agent entered a turn.
    user_message: bool
    # A ``system``/``turn_duration`` line landed → the turn is over.
    turn_end: bool
    # New tool_result content landed → any pending TUI prompt was answered.
    tool_results: bool
