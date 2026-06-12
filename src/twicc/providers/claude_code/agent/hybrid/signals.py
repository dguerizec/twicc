"""JSONL-derived state signals for hybrid sessions.

Produced by the Claude Code sessions watcher (one per ingest batch with
fresh lines, hybrid sessions only) and consumed by
``ClaudeCodeAgentManager.handle_hybrid_jsonl_signals``.

Also home to ``HybridHookOutcome``, the routing verdict the manager returns
to the hooks watcher for each event file.
"""

from enum import StrEnum
from typing import NamedTuple


class HybridHookOutcome(StrEnum):
    """What the hooks watcher should do with an event file after routing.

    UNHANDLED: stale event (no live hybrid agent) or unrouted event name —
        delete the file, never retry.
    HANDLED: routed, but the file is no longer needed — delete it.
    OWNED: routed and the agent registered a pending request keyed on the
        event's nonce — the drop file must STAY on disk (the agent deletes
        it at resolution/clear/death; the boot scan re-feeds survivors after
        a TwiCC restart, restoring the GUI-answer widget).
    """

    UNHANDLED = "unhandled"
    HANDLED = "handled"
    OWNED = "owned"


class HybridJsonlSignals(NamedTuple):
    # A real user prompt landed (computed ItemKind.USER_MESSAGE — excludes
    # meta lines, tool results and system XML) → the agent entered a turn.
    user_message: bool
    # A ``system``/``turn_duration`` line landed → the turn is over.
    turn_end: bool
    # New tool_result content landed → any pending TUI prompt was answered.
    tool_results: bool
