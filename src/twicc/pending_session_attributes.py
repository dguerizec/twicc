"""
Pending per-session structural attributes buffer (hidden + spawned_by).

Same pattern as :mod:`twicc.pending_agent_settings`, but for the two
structural attributes that fall outside the closed ``AgentSettings``
bundle: ``hidden`` and ``spawned_by_id``.

When the CLI / WS handler decides those values, the ``Session`` row
does not exist yet — it will be created by the provider's file watcher
on the first JSONL line. This module bridges the gap with a simple
in-memory keyed store, identical in spirit to the agent-settings
buffer.

- :func:`set_pending_session_attributes` is called by the create-session
  service before the manager spawns the agent process;
- :func:`pop_pending_session_attributes` is called by the watcher when
  it creates the row, and the values are forwarded to
  ``Session.objects.create(...)``.

The absence of a pending entry is signalled by ``None``.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PendingSessionAttributes(NamedTuple):
    hidden: bool
    spawned_by_id: str | None


# session_id -> PendingSessionAttributes
_pending: dict[str, PendingSessionAttributes] = {}


def set_pending_session_attributes(
    session_id: str,
    *,
    hidden: bool = False,
    spawned_by_id: str | None = None,
) -> None:
    """Store pending structural attributes to be applied at row creation."""
    _pending[session_id] = PendingSessionAttributes(
        hidden=hidden,
        spawned_by_id=spawned_by_id,
    )
    logger.debug(
        "Set pending session attributes for %s: hidden=%s spawned_by_id=%s",
        session_id, hidden, spawned_by_id,
    )


def pop_pending_session_attributes(
    session_id: str,
) -> PendingSessionAttributes | None:
    """Get and remove the pending attributes for a session, or ``None``."""
    return _pending.pop(session_id, None)
