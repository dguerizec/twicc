"""
Pending per-session title buffer (provider-agnostic).

When the front sends a custom title together with the very first
message of a new session (a draft becoming real), the corresponding
provider session file does not exist yet — it will be created when
the provider's process starts. This module bridges the gap with a
simple in-memory keyed store:

- :func:`set_pending_title` is called by the WS handler when a new
  session is sent with a title;
- :func:`get_pending_title` is consumed by the session serializer so
  the front sees the chosen title immediately, without waiting for
  the provider to absorb it;
- :func:`pop_pending_title` is consumed by the provider's agent
  manager once the session file is ready, just before persisting
  the title to the provider's backing store.
"""

import logging

logger = logging.getLogger(__name__)

# session_id -> title
_pending: dict[str, str] = {}


def set_pending_title(session_id: str, title: str) -> None:
    """Store a pending title for a draft session (provider file not yet created)."""
    _pending[session_id] = title
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Return the pending title for ``session_id``, or ``None`` if none pending. Does not remove it."""
    return _pending.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove the pending title for ``session_id``."""
    return _pending.pop(session_id, None)
