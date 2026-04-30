"""
Pending per-session settings buffer (provider-agnostic).

When the front sends a message for a new session (a draft becoming real),
the WS handler has the user-chosen :class:`AgentSettings` but the
``Session`` row does not exist yet — it will be created by the
provider's file watcher when the provider's SDK writes the first
session file. This module bridges that gap with a simple in-memory
keyed store:

- :func:`set_pending_agent_settings` is called by the WS handler before spawning the
  provider process, with the bundle of settings the user picked;
- :func:`pop_pending_agent_settings` is called by the provider's watcher when it
  creates the row, and the bundle is forwarded to ``create_session``.

The store carries one :class:`AgentSettings` per session id; the
absence of a pending entry is signalled by ``None``.
"""

import logging

from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# session_id -> AgentSettings
_pending: dict[str, AgentSettings] = {}


def set_pending_agent_settings(session_id: str, settings: AgentSettings) -> None:
    """Store pending agent settings to be applied when the session is created by the watcher."""
    _pending[session_id] = settings
    logger.debug("Set pending settings for session %s: %s", session_id, settings)


def pop_pending_agent_settings(session_id: str) -> AgentSettings | None:
    """Get and remove the pending agent settings for a session, or ``None`` if none pending."""
    return _pending.pop(session_id, None)
