"""Per-session context injection — the ``<twicc:context>`` channel.

A small, provider-agnostic facility for handing a running agent ``key: value``
facts it cannot otherwise learn at launch, by folding them into its next user
message as a single ``<twicc:context>`` block. Today the only use is Codex's
canonical ``session_id``: Codex mints it inside ``thread_start``, *after* the
system-prompt addendum has been frozen, so it cannot live in the addendum the
way it does for Claude Code (which knows its id up front).

The facility is generic across providers, in three parts:

1. **Registry** (:func:`inject_context`) — any code, at any time, queues fields
   for a session. Same in-memory, keyed-by-session-id shape as
   :mod:`twicc.pending_titles` & friends; touched only from the agent event
   loop, so no locking.

2. **Send-time fold** (:func:`apply_pending_context`) — each provider calls
   this where it composes an outgoing user message, to prepend one merged
   ``<twicc:context>`` block built from everything queued, then clear it
   (one-shot). Each provider folds in the one method every outgoing user
   message passes through, so a queued injection lands on the next message
   whether it opens a fresh turn or is sent mid-turn (steer / queued). Current
   integration points — add one when a new provider lands:

     - Codex:       ``CodexAgent._build_turn_input`` (a normal turn and a steer)
     - Claude Code: ``ClaudeCodeAgent._build_query_prompt`` (start and send)

   One-shot is enough: the block lands on the next user message only; the agent
   then carries it in its own conversation history (and, for Codex, its
   replayed rollout), and the ``twicc-whoami`` skill stays the runtime fallback
   — so the block is not repeated on every turn. :func:`clear_context` runs from
   the shared agent teardown (``BaseAgent._transition_to_dead``) to drop
   anything an agent that died before its first message never consumed.

3. **Ingestion strip** (:func:`strip_context_blocks_in_place`) — the watcher /
   batch compute scrubs the block from the copy TwiCC persists
   (``SessionItem.content``) in ``providers/compute_base.py``, generically for
   every provider, so the UI, full-text search, session title and message
   browser never show it. The agent still saw the block in its turn input and
   replayed rollout; only the stored copy is cleaned.

The block carries arbitrary ``key: value`` lines, so a new context field is a
one-line change at the injection site and needs no change here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

logger = logging.getLogger(__name__)

CONTEXT_TAG_NAME = "twicc:context"

# Cheap membership pre-check callers run before the (rarely-needed) strip, so
# the common case — an item without the tag — never pays for a regex or a walk.
CONTEXT_BLOCK_MARKER = f"<{CONTEXT_TAG_NAME}>"

# Matches a leading ``<twicc:context> ... </twicc:context>`` block plus the
# whitespace that separates it from the real message. Anchored at the start so
# a tag the user happens to type mid-message is never touched; DOTALL so a
# multi-line block (one ``key: value`` per line) is captured whole; non-greedy
# so back-to-back blocks are removed one at a time rather than as one span.
_BLOCK_RE = re.compile(
    rf"^\s*<{re.escape(CONTEXT_TAG_NAME)}>.*?</{re.escape(CONTEXT_TAG_NAME)}>\s*",
    re.DOTALL,
)


# --------------------------------------------------------------------------
# Block format (shared by the injection side and the ingestion strip)
# --------------------------------------------------------------------------

def build_context_block(fields: Mapping[str, str]) -> str:
    """Compose the ``<twicc:context>`` block for ``fields`` (insertion order kept).

    Returns the block with no trailing separator — the caller joins it to the
    user text with its own spacing.
    """
    lines = "\n".join(f"{key}: {value}" for key, value in fields.items())
    return f"<{CONTEXT_TAG_NAME}>\n{lines}\n</{CONTEXT_TAG_NAME}>"


def strip_context_blocks(text: str) -> str:
    """Remove a leading ``<twicc:context>`` block from ``text`` (no-op if absent)."""
    if CONTEXT_BLOCK_MARKER not in text:
        return text
    return _BLOCK_RE.sub("", text)


def strip_context_blocks_in_place(parsed: object) -> bool:
    """Strip context blocks from every string within a parsed JSONL item.

    Walks ``parsed`` (the dict/list decoded from one JSONL line) and rewrites
    any string value that starts with a context block. Provider-agnostic: it
    does not need to know which field carries the user text — the anchored
    regex only bites the value that actually starts with the block. Returns
    ``True`` when anything changed, so the caller re-serialises
    ``SessionItem.content``.
    """
    changed = False

    def transform(value: object) -> object:
        nonlocal changed
        if isinstance(value, str):
            stripped = strip_context_blocks(value)
            if stripped != value:
                changed = True
            return stripped
        if isinstance(value, dict):
            for key, child in value.items():
                value[key] = transform(child)
            return value
        if isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = transform(child)
            return value
        return value

    transform(parsed)
    return changed


# --------------------------------------------------------------------------
# Pending registry (one-shot, keyed by session id)
# --------------------------------------------------------------------------

# session_id -> ordered {key: value} of fields awaiting the next user message.
_pending: dict[str, dict[str, str]] = {}


def inject_context(session_id: str, /, **fields: object) -> None:
    """Queue context ``fields`` for the session's next user message.

    ``session_id`` is positional-only so a field may itself be named
    ``session_id`` without colliding with the registry key — e.g. the canonical
    use ``inject_context(thread_id, session_id=thread_id)``.

    Merges into anything already queued for the session (last write wins per
    key). Values are coerced to ``str`` so callers can pass ids/numbers without
    ceremony. An empty call is a no-op.
    """
    if not fields:
        return
    bucket = _pending.setdefault(session_id, {})
    for key, value in fields.items():
        bucket[key] = str(value)
    logger.debug(
        "Queued context injection for %s: keys=%s",
        session_id, sorted(bucket.keys()),
    )


def consume_context(session_id: str) -> dict[str, str] | None:
    """Return and remove the queued fields for a session, or ``None``.

    One-shot: after this call the session has nothing pending until the next
    :func:`inject_context`.
    """
    return _pending.pop(session_id, None)


def clear_context(session_id: str) -> None:
    """Drop any queued fields for a session (best-effort teardown cleanup).

    Covers the rare case of a session that gets a queued injection but dies
    before its first turn ever consumes it; the normal path consumes the entry
    on the first turn, so this is usually a no-op.
    """
    _pending.pop(session_id, None)


def apply_pending_context(session_id: str, text: str) -> str:
    """Fold any queued context for ``session_id`` into the user-message ``text``.

    Consumes the registry (one-shot) and prepends a single ``<twicc:context>``
    block built from every queued field. Returns ``text`` unchanged when
    nothing is queued. Generic across providers — the caller just hands it the
    user-message text at send time.
    """
    fields = consume_context(session_id)
    if not fields:
        return text
    block = build_context_block(fields)
    return f"{block}\n\n{text}" if text else block
