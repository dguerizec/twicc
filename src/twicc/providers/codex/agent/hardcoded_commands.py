"""Hardcoded slash commands for Codex sessions.

Unlike Claude Code — whose ``/compact``, ``/init``, … are interpreted
natively by the CLI once the raw text reaches it — the Codex CLI has no
slash-command vocabulary of its own (it reserves ``$`` for skills). So TwiCC
captures a small set of ``/`` commands itself: the manager parses them off
the outgoing user text at the single convergence point every send path funnels
through (:meth:`CodexAgentManager.send_to_session` — hit by the WebSocket, the
CLI ``send-message`` drop-file, …) and routes them to a direct SDK action
instead of letting them become a normal turn.

This module owns the **capture** side: the command vocabulary
(:data:`KNOWN_COMMANDS`) and the parser (:func:`parse_hardcoded_command`). The
**execution** side lives on :class:`~twicc.providers.codex.agent.agent.CodexAgent`
(``run_hardcoded_command`` → one action method per command). The frontend keeps
a display-only copy of the list in ``providers/codex/helpers.js``
(``getBuiltInCommands``) for the ``/`` autocomplete; capture and execution stay
fully backend.

Adding a command:
  1. add its name to :data:`KNOWN_COMMANDS`,
  2. add a branch + action method in ``CodexAgent.run_hardcoded_command``,
  3. add a display entry to ``BUILTIN_COMMANDS`` in the frontend helpers.
"""

from __future__ import annotations

from typing import NamedTuple


class HardcodedCommand(NamedTuple):
    """A parsed ``/command`` plus its trailing argument string (may be empty)."""

    name: str
    args: str


# The ``/`` command vocabulary TwiCC handles for Codex. A flat set because the
# only thing the parser needs is membership; the per-command action lives on
# the agent. ``compact`` → server-side context compaction
# (``thread/compact/start`` RPC); it takes no arguments — the SDK
# ``thread_compact`` accepts only the thread id — so any trailing text is
# parsed into ``args`` but ignored by the handler.
KNOWN_COMMANDS: frozenset[str] = frozenset({"compact"})


def parse_hardcoded_command(text: str) -> HardcodedCommand | None:
    """Return the hardcoded command a user message invokes, or ``None``.

    A match requires the message to start with ``/`` (after surrounding
    whitespace) and its first whitespace-delimited token to be a name in
    :data:`KNOWN_COMMANDS`. Everything after that token is returned verbatim
    as ``args`` (trimmed). Non-matches — empty text, a leading ``/`` followed
    by an unknown token (``/some/path``, ``/compactify``), or no leading ``/``
    at all — return ``None`` so the caller treats the text as an ordinary
    message.

    Collision-free for Codex: the CLI gives ``/`` no native meaning (it
    reserves ``$`` for skills), so intercepting these names steals nothing a
    user could legitimately want to send to the model.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    # Drop the leading "/", then split into (name, rest). ``split(maxsplit=1)``
    # breaks on any run of whitespace — spaces, tabs, newlines — so a command
    # followed by a newline-separated body still resolves its name.
    parts = stripped[1:].split(maxsplit=1)
    if not parts:
        return None
    name = parts[0]
    if name not in KNOWN_COMMANDS:
        return None
    args = parts[1].strip() if len(parts) > 1 else ""
    return HardcodedCommand(name=name, args=args)
