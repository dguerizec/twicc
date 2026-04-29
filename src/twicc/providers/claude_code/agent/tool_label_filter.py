"""Filtering rules for tool inputs broadcast as part of the active-tools status feed.

We strip large fields that are useless for the UI status line ("Claude is …") to
keep the WebSocket payload small. Anything not listed in INPUT_DENYLIST passes
through unchanged.
"""

from typing import Any

INPUT_DENYLIST: dict[str, frozenset[str]] = {
    "Edit": frozenset({"old_string", "new_string"}),
    "MultiEdit": frozenset({"edits"}),
    "Write": frozenset({"content"}),
    "Task": frozenset({"prompt"}),
    "Agent": frozenset({"prompt"}),
}


def filter_tool_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``tool_input`` with large fields removed for the given tool."""
    deny = INPUT_DENYLIST.get(tool_name)
    if not deny:
        return dict(tool_input)
    return {k: v for k, v in tool_input.items() if k not in deny}
