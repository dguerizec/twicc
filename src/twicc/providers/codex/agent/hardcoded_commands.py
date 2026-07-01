"""Hardcoded slash commands for Codex sessions.

Unlike Claude Code — whose ``/compact``, ``/init``, … are interpreted
natively by the CLI once the raw text reaches it — the Codex CLI has no
slash-command vocabulary of its own (it reserves ``$`` for skills). So TwiCC
captures a small set of ``/`` commands itself: the manager parses them off the
outgoing user text and routes them to a direct SDK action instead of letting
them become a normal turn. Capture happens at BOTH manager entry points, so a
command is intercepted whether it targets an existing session or starts a new
one:
  - :meth:`CodexAgentManager.send_to_session` — existing sessions (WebSocket,
    CLI ``send-message`` drop-file, …).
  - :meth:`CodexAgentManager.create_session` — a command as the FIRST message
    of a brand-new session (e.g. ``/goal`` typed in a fresh draft). Without
    this the text would reach the model as ordinary turn text.

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

A command meaningful as a first message (like ``/goal``) is already covered by
the ``create_session`` capture; one that only makes sense on an existing
session (like ``/compact``) is just a degenerate no-op there.
"""

from __future__ import annotations

from typing import NamedTuple


class HardcodedCommand(NamedTuple):
    """A parsed ``/command`` plus its trailing argument string (may be empty)."""

    name: str
    args: str


# The ``/`` command vocabulary TwiCC handles for Codex. A flat set because the
# only thing the parser needs is membership; the per-command action lives on
# the agent.
#
# - ``compact`` → server-side context compaction (``thread/compact/start``
#   RPC); it takes no arguments — the SDK ``thread_compact`` accepts only the
#   thread id — so any trailing text is parsed into ``args`` but ignored.
# - ``goal`` → set/clear the thread's goal via the ``thread/goal/{get,set,clear}``
#   app-server RPCs. It DOES use ``args``: ``/goal clear`` clears, any other
#   non-empty text is the objective to set, a bare ``/goal`` is a usage error.
KNOWN_COMMANDS: frozenset[str] = frozenset({"compact", "goal"})


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
