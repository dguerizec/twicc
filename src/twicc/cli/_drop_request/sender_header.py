"""Sender-identity header for inter-session messages.

A ``send-message`` / ``send-messages`` issued from inside a TwiCC agent (CLI
via PID ancestry, MCP via the forced session id) delivers its text prefixed
with a blockquote header naming the sender — otherwise the recipient would
read an anonymous follow-up from "the user" with no way to tell another
session is talking. A human invoking the CLI from a plain shell resolves to
no caller session, so no header is added.

The wording encodes the spawn-tree relation between the two sessions, from
the recipient's point of view::

    > Message from your spawned session <id> ("<title>")   # caller is a child of the recipient
    > Message from your parent session <id> ("<title>")    # caller spawned the recipient
    > Message from a sibling session <id> ("<title>")      # same spawner on both sides
    > Message from another session <id> ("<title>")        # no spawn-tree relation
    ---
    <original text>

The ``("<title>")`` segment is omitted when the caller has no title yet.
"""

from __future__ import annotations

TITLE_MAX_CHARS = 80


def prefix_sender_header(
    text: str,
    caller,
    *,
    recipient_id: str,
    recipient_spawned_by_id: str | None,
) -> str:
    """Return ``text`` prefixed with the sender header, or unchanged.

    ``caller`` is the calling agent's ``Session`` row (or ``None`` when the
    command runs outside a TwiCC agent). No header is added when there is no
    caller, or on a self-send (a session messaging itself needs no
    attribution).
    """
    if caller is None or caller.id == recipient_id:
        return text

    if caller.spawned_by_id is not None and caller.spawned_by_id == recipient_id:
        relation = "your spawned session"
    elif recipient_spawned_by_id is not None and recipient_spawned_by_id == caller.id:
        relation = "your parent session"
    elif caller.spawned_by_id is not None and caller.spawned_by_id == recipient_spawned_by_id:
        relation = "a sibling session"
    else:
        relation = "another session"

    title = (caller.title or "").strip()
    if len(title) > TITLE_MAX_CHARS:
        title = title[: TITLE_MAX_CHARS - 1] + "…"
    suffix = f' ("{title}")' if title else ""

    return f"> Message from {relation} {caller.id}{suffix}\n---\n{text}"
