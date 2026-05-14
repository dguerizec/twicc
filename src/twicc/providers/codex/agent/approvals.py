"""
Codex approval helpers — pure functions translating between the Codex
JSON-RPC wire format and TwiCC's provider-neutral ``PendingRequest``.

The 3 approval methods Codex sends as ``server requests`` (i.e. with an
``id``, requiring a synchronous response):

- ``item/commandExecution/requestApproval`` — shell exec / sub-exec / network
- ``item/fileChange/requestApproval`` — ApplyPatch (one or more file changes)
- ``item/permissions/requestApproval`` — model asks for extra filesystem / network permissions

Other server requests (``item/tool/call``, ``account/chatgptAuthTokens/refresh``,
``item/tool/requestUserInput``, ``mcpServer/elicitation/request``) are NOT
ours to handle in PR2a — the wiring in :class:`CodexAgent` delegates them to
the SDK's default sync handler (captured before we monkey-patch). See spec
``§1.6`` and ``§7-Q9``.

Wire details: see spec ``§1.1.{a,b,c}``. Decision types: ``§9`` (annex).
"""

from __future__ import annotations

import time
import uuid

from twicc.agent.states import PendingRequest

# Method (wire) → human-readable tool_name we expose in PendingRequest.
# The tool_name is what the frontend dispatches on (in a later PR) to pick
# the right body component. Keeping it short and hyphen-free.
APPROVAL_METHODS: dict[str, str] = {
    "item/commandExecution/requestApproval": "commandExecution",
    "item/fileChange/requestApproval":       "fileChange",
    "item/permissions/requestApproval":      "permissions",
}

# Wire response used by the sync handler when the future is cancelled
# (typical: kill while waiting on a user click). Sending ``decline``
# instead of ``cancel`` keeps the turn alive — the model can recover or
# pick another approach instead of being aborted whole-cloth.
_DEFAULT_KILL_RESPONSE_COMMAND_OR_FILE: dict = {"decision": "decline"}

# Permissions has a different wire shape — see spec ``§1.1.c``.
_DEFAULT_KILL_RESPONSE_PERMISSIONS: dict = {
    "permissions": {},  # empty granted profile = nothing accorded
    "scope": "turn",
}


def is_approval_method(method: str) -> bool:
    """Return True if ``method`` is one of the 3 approval RPCs we own."""
    return method in APPROVAL_METHODS


def derive_request_id(params: dict | None) -> str:
    """Build a stable key to route the user response back to the right future.

    Codex sometimes fans a single ``itemId`` (e.g. an ``ExecCommandBegin``)
    into several sub-exec approvals, each carrying its own ``approvalId``
    (vérifié dans the schema description, ``ServerRequest.json:345-442``).
    Prefer ``approvalId`` when present, fall back to ``itemId``, and as a
    last-ditch produce a UUID so we never collide on empty payloads.
    """
    if not params:
        return str(uuid.uuid4())
    candidate = params.get("approvalId") or params.get("itemId")
    return candidate if isinstance(candidate, str) and candidate else str(uuid.uuid4())


def make_pending_request(method: str, params: dict | None) -> PendingRequest:
    """Translate a Codex server-request into the provider-neutral PendingRequest.

    Callers are expected to enrich ``params`` upstream if they want to
    attach side-band info — e.g. the streamed item payload for
    ``fileChange`` (which carries the diff). See
    :meth:`CodexAgent._enrich_params_with_item_payload`.
    """
    tool_name = APPROVAL_METHODS[method]
    return PendingRequest(
        request_id=derive_request_id(params),
        request_type="tool_approval",  # Codex never uses ``ask_user_question``
        tool_name=tool_name,
        tool_input=dict(params) if params else {},
        created_at=time.time(),
        permission_suggestions=None,
    )


def default_response_for(method: str) -> dict:
    """Wire response we send to Codex when we cannot route the request to a user.

    Triggered by the sync handler's ``CancelledError`` branch on kill /
    transport teardown — NOT by user-initiated ``Cancel turn`` (that goes
    through ``resolve_pending_request`` with the real wire decision).

    Returns a shape that's valid for the requested ``method``:
    - command / file: ``{"decision": "decline"}``
    - permissions:    ``{"permissions": {}, "scope": "turn"}``
    """
    if method == "item/permissions/requestApproval":
        return dict(_DEFAULT_KILL_RESPONSE_PERMISSIONS)
    return dict(_DEFAULT_KILL_RESPONSE_COMMAND_OR_FILE)
