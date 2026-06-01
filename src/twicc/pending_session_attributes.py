"""
Pending per-session structural attributes buffer.

Same pattern as :mod:`twicc.pending_agent_settings`, but for structural
attributes and annotations that fall outside the closed ``AgentSettings``
bundle: ``hidden``, spawned-session links, and free-form annotations.

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
    spawn_root_id: str | None
    annotations: dict
    # TwiCC system-prompt addendum, composed at session creation time and
    # carried through to the row by the watcher. See the comment on
    # ``Session.system_prompt_addendum`` in :mod:`twicc.core.models`.
    system_prompt_addendum: str | None


# session_id -> PendingSessionAttributes
_pending: dict[str, PendingSessionAttributes] = {}


def set_pending_session_attributes(
    session_id: str,
    *,
    hidden: bool = False,
    spawned_by_id: str | None = None,
    spawn_root_id: str | None = None,
    annotations: dict | None = None,
    system_prompt_addendum: str | None = None,
) -> None:
    """Store pending structural attributes to be applied at row creation."""
    _pending[session_id] = PendingSessionAttributes(
        hidden=hidden,
        spawned_by_id=spawned_by_id,
        spawn_root_id=spawn_root_id,
        annotations=annotations or {},
        system_prompt_addendum=system_prompt_addendum,
    )
    logger.debug(
        "Set pending session attributes for %s: hidden=%s spawned_by_id=%s "
        "spawn_root_id=%s annotations_keys=%s addendum_len=%s",
        session_id, hidden, spawned_by_id, spawn_root_id,
        sorted((annotations or {}).keys()),
        len(system_prompt_addendum) if system_prompt_addendum else 0,
    )


def pop_pending_session_attributes(
    session_id: str,
) -> PendingSessionAttributes | None:
    """Get and remove the pending attributes for a session, or ``None``."""
    return _pending.pop(session_id, None)


def get_pending_session_attributes(
    session_id: str,
) -> PendingSessionAttributes | None:
    """Read the pending attributes without consuming them, or ``None``.

    Use this from code that needs to look at the values before the watcher
    consumes them at row creation (e.g. composing the system prompt addendum
    at agent start time, when the ``Session`` row does not exist yet).
    """
    return _pending.get(session_id)
