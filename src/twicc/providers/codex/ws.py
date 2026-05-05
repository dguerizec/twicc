"""
Codex provider WebSocket handler.

Currently handles auth-only traffic:
- emits ``codex:auth_updated`` on each new connection (initial state push);
- routes the inbound ``codex:check_auth`` action (manual "Check again"
  from the UI) to a forced re-check + broadcast.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from .auth import check_and_broadcast, get_auth_message_for_connection

logger = logging.getLogger(__name__)


class CodexWSHandler:
    """Routes Codex-specific WebSocket messages to dedicated handlers.

    Instantiated once per WebSocket connection by the main ``WSConsumer``,
    which passes itself in so handlers can call ``self.consumer.send_json()``,
    access ``self.consumer.channel_layer``, etc.
    """

    def __init__(self, consumer):
        self.consumer = consumer

    async def get_connect_messages(self) -> AsyncIterator[dict]:
        """Yield messages to send to a newly connected client."""
        yield await get_auth_message_for_connection()

    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Codex-prefixed message."""
        if action == "check_auth":
            # Forced re-check of Codex CLI auth state, broadcast to every client.
            await check_and_broadcast(force=True)
            return True

        return False
