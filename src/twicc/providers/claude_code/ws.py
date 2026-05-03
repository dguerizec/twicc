"""WebSocket message handling specific to the Claude Code provider.

Provider handlers are plain classes (not Channels consumers) instantiated
once per WebSocket connection by ``twicc.asgi.WSConsumer``. The main
consumer routes provider-specific messages (whose ``type`` field is
prefixed with ``"<provider_key>:"``) to the matching handler.

Both inbound actions (via ``dispatch``) and outbound on-connect messages
(via ``get_connect_messages``) use the ``claude_code:`` prefix, e.g.
``claude_code:pending_request_response`` (inbound),
``claude_code:auth_updated`` (outbound).
"""

import logging
from collections.abc import AsyncIterator

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionRuleValue,
    PermissionUpdate,
)

from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager
from twicc.providers.claude_code.auth import (
    check_and_broadcast as check_auth_and_broadcast,
    get_auth_message_for_connection,
)
from twicc.providers.claude_code.statuspage_task import get_statuspage_message_for_connection
from twicc.usage_task import get_usage_message_for_connection

logger = logging.getLogger(__name__)


def _permission_update_from_dict(data: dict) -> PermissionUpdate:
    """Reconstruct a PermissionUpdate from its serialized dict form.

    The SDK's ``PermissionUpdate.to_dict()`` uses camelCase keys (e.g., ``toolName``,
    ``ruleContent``). This function reverses that conversion back to the dataclass
    with snake_case field names.

    Args:
        data: Dictionary as produced by ``PermissionUpdate.to_dict()``

    Returns:
        A ``PermissionUpdate`` instance ready to pass back to the SDK.
    """
    rules = None
    raw_rules = data.get("rules")
    if raw_rules is not None:
        rules = [
            PermissionRuleValue(
                tool_name=r["toolName"],
                # SDK bug workaround: PermissionUpdate.to_dict() serializes None as
                # "ruleContent": null, but Claude Code CLI's Zod schema rejects null
                # (expects string | undefined). Using "" instead of None avoids the error.
                rule_content=r.get("ruleContent") or "",
            )
            for r in raw_rules
        ]

    return PermissionUpdate(
        type=data["type"],
        rules=rules,
        behavior=data.get("behavior"),
        mode=data.get("mode"),
        directories=data.get("directories"),
        destination=data.get("destination"),
    )


async def update_session_permission_mode(session_id: str, permission_mode: str) -> None:
    """Update the permission_mode for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(permission_mode=permission_mode).update
    )(permission_mode=permission_mode)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with permission_mode {permission_mode}")


class ClaudeCodeWSHandler:
    """Routes Claude Code-specific WebSocket messages to dedicated handlers.

    Instantiated once per WebSocket connection by the main ``WSConsumer``,
    which passes itself in so handlers can call ``self.consumer.send_json()``,
    access ``self.consumer.channel_layer``, etc.
    """

    def __init__(self, consumer):
        self.consumer = consumer

    async def get_connect_messages(self) -> AsyncIterator[dict]:
        """Yield messages to send to a newly connected client.

        Each yielded dict is a fully-formed message ready to be sent. CC-only
        messages have their ``type`` already set to ``"claude_code:<action>"``;
        cross-provider messages (e.g. ``usage_updated``) keep the generic
        type and carry the provider info inside their payload. The consumer
        applies the client's ``subscribe`` filter before sending.
        """
        # Claude Code CLI authentication state
        yield await get_auth_message_for_connection()

        # Anthropic statuspage status (only when not operational)
        status_msg = get_statuspage_message_for_connection()
        if status_msg is not None:
            yield status_msg

        # Latest Claude Code usage snapshot (wire type: ``usage_updated``)
        yield await get_usage_message_for_connection(Provider.CLAUDE_CODE)

    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Claude Code-prefixed message.

        Args:
            action: The message subtype, i.e. the part after the ``claude_code:`` prefix.
            content: The full message dict.

        Returns:
            True if the action was recognized and handled, False otherwise.
        """
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True

        if action == "check_auth":
            # Forced re-check of Claude Code CLI auth state. The result is broadcast
            # to the entire "updates" group so every connected client refreshes.
            await check_auth_and_broadcast(force=True)
            return True

        return False

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Handle a pending request response from the user.

        Routes the user's decision (tool approval or clarifying question answer)
        to the correct agent via the ClaudeCodeAgentManager.

        Expected content for tool approval:
        {
            "type": "claude_code:pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "tool_approval",
            "decision": "allow" | "deny",
            "message": "optional reason for deny",
            "updated_input": { ... }  // optional, for approve with modifications
            "updated_permissions": [ ... ]  // optional, checked permission suggestions
        }

        Expected content for ask_user_question:
        {
            "type": "claude_code:pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "ask_user_question",
            "answers": {
                "question text": "selected label or free text",
                ...
            }
        }
        """
        session_id = content.get("session_id")
        request_type = content.get("request_type")
        request_id = content.get("request_id")

        if not session_id or not request_type or not request_id:
            logger.warning(
                "pending_request_response missing required fields: "
                "session_id=%s, request_type=%s, request_id=%s",
                session_id, request_type, request_id,
            )
            return

        manager = get_claude_code_agent_manager()

        if request_type == "tool_approval":
            decision = content.get("decision")
            if decision == "allow":
                updated_input = content.get("updated_input")

                # Reconstruct accepted permission suggestions (if any) from the frontend
                updated_permissions = None
                raw_permissions = content.get("updated_permissions")
                if raw_permissions:
                    updated_permissions = [_permission_update_from_dict(p) for p in raw_permissions]

                response = PermissionResultAllow(
                    updated_input=updated_input,
                    updated_permissions=updated_permissions,
                )
                logger.debug("Tool approval allowed for session %s with responses=%s", session_id, response)
            else:
                message = content.get("message", "User denied this action")
                response = PermissionResultDeny(message=message)

        elif request_type == "ask_user_question":
            answers = content.get("answers", {})
            # Retrieve the original questions from the matching pending request
            process_info = manager.get_agent_info(session_id)
            matching = None
            if process_info is not None:
                matching = next(
                    (pr for pr in process_info.pending_requests if pr.request_id == request_id),
                    None,
                )
            if matching is None:
                logger.warning(
                    "pending_request_response: no pending request %s for session %s",
                    request_id, session_id,
                )
                return
            original_input = matching.tool_input
            response = PermissionResultAllow(
                updated_input={
                    "questions": original_input.get("questions", []),
                    "answers": answers,
                }
            )

        else:
            logger.warning(
                "pending_request_response: unknown request_type %r",
                request_type,
            )
            return

        # Persist setMode suggestions in DB so future resumes use the correct mode
        if request_type == "tool_approval" and content.get("decision") == "allow":
            raw_permissions = content.get("updated_permissions")
            if raw_permissions:
                for perm in raw_permissions:
                    if perm.get("type") == "setMode" and perm.get("mode"):
                        await update_session_permission_mode(session_id, perm["mode"])
                        logger.info(
                            "Permission mode updated to %r for session %s (from setMode suggestion)",
                            perm["mode"],
                            session_id,
                        )
                        break  # Only one setMode should be applied

        resolved = await manager.resolve_pending_request(session_id, request_id, response)
        if not resolved:
            logger.warning(
                "pending_request_response: failed to resolve request %s for session %s "
                "(no matching pending request or already resolved)",
                request_id, session_id,
            )
