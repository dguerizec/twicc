"""MCP elicitation support for the Claude Code SDK path.

The Claude Code CLI forwards MCP-server elicitations (``elicitation/create``,
form and URL modes) to its SDK consumer as a control request with
``subtype: "elicitation"`` — but only in streaming-input mode, which TwiCC
always uses (``ClaudeSDKClient``). The TypeScript SDK exposes this as the
``onElicitation`` callback; the Python SDK (0.2.x) has no equivalent and
answers any unknown subtype with an error control_response, which the CLI
converts to ``{"action": "cancel"}`` — the MCP server sees its elicitation
silently dismissed.

Until the Python SDK grows native support, this module widens
``Query._handle_control_request`` once (idempotent, on first attach): a
control request whose subtype is ``elicitation`` arriving on a Query that
carries a TwiCC handler (:data:`_HANDLER_ATTR`, attached per client by
:func:`attach_elicitation_handler`) is routed to that handler and its
``{"action", "content"?}`` result is written back as the control response.
Queries without a handler — and every other subtype — fall through to the
original method, behaviour unchanged. When a Python SDK release ships an
``on_elicitation`` equivalent, replace this bridge with the native callback.

Wire shapes (extracted from the bundled CLI, verified by live probe):

- request params: ``mcp_server_name``, ``message``, ``mode`` ("form" | "url",
  optional — absent means form), ``requested_schema`` (form), ``url`` +
  ``elicitation_id`` (url), optional ``title`` / ``display_name`` /
  ``description`` (permission-display meta).
- response: ``{"action": "accept" | "decline" | "cancel", "content": {...}}``
  — ``content`` (the filled form values) only meaningful with ``accept``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import orjson

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk._internal.query import Query

from twicc.agent.states import PendingRequest

logger = logging.getLogger(__name__)

# tool_names the frontend dispatches on. Deliberately identical to the Codex
# elicitation sub-kinds so both providers share the same body components
# (``items/shared/Elicitation{Form,Url}Body.vue``) and the same response wire
# fields (``action`` / ``content``). Claude has no ``mcpToolCall`` sub-kind:
# MCP tool-call approvals ride the regular ``can_use_tool`` path.
ELICITATION_TOOL_NAMES: frozenset[str] = frozenset({"elicitationForm", "elicitationUrl"})

# Attribute set on the Query instance to carry the per-agent handler. The
# underscore-twicc prefix guarantees no collision with SDK internals.
_HANDLER_ATTR = "_twicc_elicitation_handler"

ElicitationHandler = Callable[[dict], Awaitable[dict]]

_original_handle_control_request: Callable[..., Any] | None = None


def make_elicitation_pending_request(params: dict | None) -> PendingRequest:
    """Translate the CLI's elicitation control-request params into a PendingRequest.

    ``tool_input`` is normalised to the exact key set the shared frontend
    bodies read (the Codex wire's camelCase names): ``serverName``,
    ``message``, ``mode``, plus ``requestedSchema`` / ``url`` when present.
    An unknown ``mode`` degrades to the generic form body (visible, if plain
    — never a silent drop), mirroring the Codex resolve_tool_name fallback.

    ``request_id`` is a fresh UUID: unlike Codex approvals, the response
    travels back through the awaiting control-request coroutine, not by wire
    id correlation, so no stable derivation is needed.
    """
    params = params if isinstance(params, dict) else {}
    mode = params.get("mode") or "form"
    tool_name = "elicitationUrl" if mode == "url" else "elicitationForm"
    tool_input: dict[str, Any] = {
        "serverName": params.get("mcp_server_name") or "",
        "message": params.get("message") or "",
        "mode": mode,
    }
    if params.get("requested_schema") is not None:
        tool_input["requestedSchema"] = params["requested_schema"]
    if params.get("url") is not None:
        tool_input["url"] = params["url"]
    if params.get("elicitation_id") is not None:
        tool_input["elicitationId"] = params["elicitation_id"]
    return PendingRequest(
        request_id=str(uuid.uuid4()),
        request_type="ask_user_question",
        tool_name=tool_name,
        tool_input=tool_input,
        created_at=time.time(),
        permission_suggestions=None,
    )


def default_elicitation_response() -> dict:
    """Safe wire response when the user's answer cannot be validated.

    ``cancel`` (not decline): a malformed payload is not a user decision —
    it maps to the MCP "dismissed" action, same as the CLI's own fallback.
    """
    return {"action": "cancel"}


def _install_bridge() -> None:
    """Widen ``Query._handle_control_request`` for the ``elicitation`` subtype.

    Idempotent — the original method is captured once and every other code
    path (other subtypes, Queries without a handler) delegates to it
    verbatim. The success / error control_response envelopes mirror the
    SDK's own ``_handle_control_request`` shapes; ``CancelledError``
    propagates without writing a response, exactly like the SDK (the CLI
    abandoned the request via ``control_cancel_request``, or the transport
    is being torn down).
    """
    global _original_handle_control_request
    if _original_handle_control_request is not None:
        return
    _original_handle_control_request = Query._handle_control_request

    async def _handle_control_request(self: Query, request: dict) -> None:
        request_data = request.get("request") or {}
        if request_data.get("subtype") == "elicitation":
            handler: ElicitationHandler | None = getattr(self, _HANDLER_ATTR, None)
            if handler is not None:
                await _respond_to_elicitation(self, request, handler)
                return
        await _original_handle_control_request(self, request)

    Query._handle_control_request = _handle_control_request
    logger.info("Claude Code SDK elicitation bridge installed")


async def _respond_to_elicitation(
    query: Query, request: dict, handler: ElicitationHandler,
) -> None:
    request_id = request["request_id"]
    try:
        response_data = await handler(request["request"])
        envelope = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response_data,
            },
        }
    except Exception as e:
        # Error-shaped response: the CLI converts it to {"action": "cancel"}
        # for the MCP server, so the elicitation resolves instead of hanging.
        logger.error(
            "Elicitation handler failed for control request %s: %s",
            request_id, e, exc_info=True,
        )
        envelope = {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": request_id,
                "error": str(e),
            },
        }
    await query.transport.write(orjson.dumps(envelope).decode() + "\n")


def attach_elicitation_handler(client: ClaudeSDKClient, handler: ElicitationHandler) -> None:
    """Route this client's MCP elicitations to ``handler``.

    Must be called after ``client.connect()`` (the Query only exists from
    there); elicitations cannot arrive earlier — they only fire mid-turn.
    Installs the class-level bridge on first use.
    """
    _install_bridge()
    query = client._query
    if query is None:  # pragma: no cover - connect() precondition violated
        raise RuntimeError("attach_elicitation_handler called before client.connect()")
    setattr(query, _HANDLER_ATTR, handler)
