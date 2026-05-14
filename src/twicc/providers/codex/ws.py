"""
Codex provider WebSocket handler.

Handles auth + usage + statuspage traffic:
- emits ``codex:auth_updated``, the latest usage snapshot, and (when
  not operational) ``codex:openai_status`` on each new connection;
- routes the inbound ``codex:check_auth`` action (manual "Check again"
  from the UI) to a forced re-check + broadcast.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from twicc.core.enums import Provider
from twicc.providers.codex.statuspage_task import get_statuspage_message_for_connection
from twicc.usage_task import get_usage_message_for_connection

from .auth import check_and_broadcast, get_auth_message_for_connection

from twicc.agent.registry import get_agent_manager_registry

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
        # Latest Codex usage snapshot (wire type: ``usage_updated``)
        yield await get_usage_message_for_connection(Provider.CODEX)

        # OpenAI statuspage status (only when not operational)
        status_msg = get_statuspage_message_for_connection()
        if status_msg is not None:
            yield status_msg

    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Codex-prefixed message."""
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True

        if action == "check_auth":
            # Forced re-check of Codex CLI auth state, broadcast to every client.
            await check_and_broadcast(force=True)
            return True

        return False

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Route the user's decision to the right agent's right future.

        Wire shape (frontend → backend, spec §9.3, §9.5):

            {
                "type": "codex:pending_request_response",
                "session_id": "...",
                "request_id": "...",
                "tool_name": "commandExecution" | "fileChange" | "permissions",
                "decision": <string-or-dict-variant>,  # see _build_codex_response
                "permissions": {...},   // permissions only
                "scope": "turn" | "session",  // permissions only
            }

        Invalid / unroutable messages are logged and dropped; we never raise
        through the WS layer (that would tear down the consumer).
        """
        session_id = content.get("session_id")
        request_id = content.get("request_id")
        tool_name = content.get("tool_name")

        if not session_id or not request_id or not tool_name:
            logger.warning(
                "codex:pending_request_response missing required fields "
                "(session_id=%r, request_id=%r, tool_name=%r)",
                session_id, request_id, tool_name,
            )
            return

        response = self._build_codex_response(tool_name, content)
        if response is None:
            # Validation failed; _build_codex_response already logged.
            # Resolve with a safe default so the SDK isn't left hanging.
            response = self._safe_default_for(tool_name)

        manager = get_agent_manager_registry().get(Provider.CODEX)
        resolved = await manager.resolve_pending_request(
            session_id, request_id, response,
        )
        if not resolved:
            logger.warning(
                "codex:pending_request_response: failed to resolve %r for session %r "
                "(no matching pending request, or already resolved)",
                request_id, session_id,
            )

    # ------------------------------------------------------------------
    # Validation + response builders (spec §7-Q11: strict)
    # ------------------------------------------------------------------

    # Decisions a string-decision approval (command + file) may carry.
    _SIMPLE_STRING_DECISIONS: set[str] = {
        "accept", "acceptForSession", "decline", "cancel",
    }
    # Object-variant keys for command (network and execpolicy amendments).
    # The mapped tuple is (expected inner-payload key, expected inner-value
    # type) — see spec §1.1.a for the wire shape of each variant.
    _COMMAND_DICT_VARIANTS: dict[str, tuple[str, type]] = {
        "acceptWithExecpolicyAmendment": ("execpolicy_amendment", list),
        "applyNetworkPolicyAmendment":   ("network_policy_amendment", dict),
    }
    _PERMISSIONS_SCOPES: set[str] = {"turn", "session"}

    def _build_codex_response(self, tool_name: str, content: dict) -> dict | None:
        """Convert the frontend payload to the SDK-wire response dict.

        Returns ``None`` on any validation failure (caller substitutes a
        safe default). Validation rules:
        - command: ``decision`` is either in :attr:`_SIMPLE_STRING_DECISIONS`
          or a dict with exactly one key from :attr:`_COMMAND_DICT_VARIANTS`.
        - file: ``decision`` is in :attr:`_SIMPLE_STRING_DECISIONS` minus
          dict variants (no amendments for file changes — see spec §1.1.b).
        - permissions: ``scope`` ∈ :attr:`_PERMISSIONS_SCOPES`, ``permissions``
          is a dict (may be empty).
        """
        decision = content.get("decision")

        if tool_name == "commandExecution":
            return self._build_command_response(decision)

        if tool_name == "fileChange":
            return self._build_file_response(decision)

        if tool_name == "permissions":
            return self._build_permissions_response(content)

        logger.error(
            "codex:pending_request_response: unknown tool_name=%r in %r",
            tool_name, content,
        )
        return None

    def _build_command_response(self, decision: object) -> dict | None:
        if isinstance(decision, str):
            if decision in self._SIMPLE_STRING_DECISIONS:
                return {"decision": decision}
            logger.error(
                "codex commandExecution: invalid string decision=%r", decision,
            )
            return None
        if isinstance(decision, dict):
            keys = list(decision.keys())
            if len(keys) != 1 or keys[0] not in self._COMMAND_DICT_VARIANTS:
                logger.error(
                    "codex commandExecution: invalid dict decision=%r "
                    "(expected exactly one key from %r)",
                    decision, sorted(self._COMMAND_DICT_VARIANTS),
                )
                return None
            variant = keys[0]
            inner = decision[variant]
            if not isinstance(inner, dict):
                logger.error(
                    "codex commandExecution: invalid inner payload for %r — "
                    "expected dict, got %r",
                    variant, type(inner).__name__,
                )
                return None
            inner_key, inner_type = self._COMMAND_DICT_VARIANTS[variant]
            inner_value = inner.get(inner_key)
            if not isinstance(inner_value, inner_type):
                logger.error(
                    "codex commandExecution: invalid inner payload for %r — "
                    "missing or wrong-type %r (expected %s, got %r)",
                    variant, inner_key, inner_type.__name__, type(inner_value).__name__,
                )
                return None
            # Wrap verbatim — Codex expects {"decision": {<variant>: {...}}}.
            return {"decision": decision}
        logger.error("codex commandExecution: invalid decision type=%r", type(decision))
        return None

    def _build_file_response(self, decision: object) -> dict | None:
        if isinstance(decision, str) and decision in self._SIMPLE_STRING_DECISIONS:
            return {"decision": decision}
        logger.error(
            "codex fileChange: invalid decision=%r (must be one of %r — "
            "no amendments allowed for file changes)",
            decision, self._SIMPLE_STRING_DECISIONS,
        )
        return None

    def _build_permissions_response(self, content: dict) -> dict | None:
        scope = content.get("scope")
        permissions = content.get("permissions")
        if scope not in self._PERMISSIONS_SCOPES:
            logger.error(
                "codex permissions: invalid scope=%r (expected %r)",
                scope, self._PERMISSIONS_SCOPES,
            )
            return None
        if not isinstance(permissions, dict):
            logger.error(
                "codex permissions: invalid permissions type=%r (expected dict)",
                type(permissions),
            )
            return None
        # ``strictAutoReview`` is optional + boolean per spec §1.1.c.
        strict_auto_review = content.get("strictAutoReview")
        response: dict = {"permissions": permissions, "scope": scope}
        if isinstance(strict_auto_review, bool):
            response["strictAutoReview"] = strict_auto_review
        return response

    def _safe_default_for(self, tool_name: str) -> dict:
        """Wire-safe fallback when the frontend response failed validation."""
        if tool_name == "permissions":
            return {"permissions": {}, "scope": "turn"}
        return {"decision": "decline"}
