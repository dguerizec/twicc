"""
Codex approval helpers — pure functions translating between the Codex
JSON-RPC wire format and TwiCC's provider-neutral ``PendingRequest``.

The 5 approval methods Codex sends as ``server requests`` (i.e. with an
``id``, requiring a synchronous response) that this bridge owns:

- ``item/commandExecution/requestApproval`` — shell exec / sub-exec / network
- ``item/fileChange/requestApproval`` — ApplyPatch (one or more file changes)
- ``item/permissions/requestApproval`` — model asks for extra filesystem / network permissions
- ``mcpServer/elicitation/request`` — MCP elicitation; 3 sub-kinds distinguished
  by :func:`resolve_tool_name` from the params, not the method:
  - an MCP-tool-call approval (``mode=form`` + ``_meta.codex_approval_kind ==
    "mcp_tool_call"``) → ``mcpToolCall``, surfaced as a ``tool_approval``
  - a generic form elicitation (``mode=form``, any other/missing ``_meta``)
    → ``elicitationForm``, surfaced as ``ask_user_question``
  - a URL elicitation (``mode=url``) → ``elicitationUrl``, surfaced as
    ``ask_user_question``
- ``item/tool/requestUserInput`` — Codex's generic "ask the user a
  question" fallback (used e.g. when an MCP tool call needs approval but the
  server hasn't opted into elicitations) → ``toolRequestUserInput``,
  surfaced as ``ask_user_question``

Other server requests (``item/tool/call``, ``account/chatgptAuthTokens/refresh``)
are NOT ours to handle — the wiring in :class:`CodexAgent` delegates them to
the SDK's default sync handler (captured before we monkey-patch). See spec
``§1.6`` and ``§7-Q9``.

Wire details: see spec ``§1.1.{a,b,c}``. Decision types: ``§9`` (annex).
"""

from __future__ import annotations

import os
import time
import uuid

from twicc.agent.states import PendingRequest

# ``commandActions[].type`` values that name a concrete, scoped filesystem
# operation we can vouch for. Anything else (notably ``unknown`` — which Codex
# emits for writes and any command it could not statically classify) means we
# cannot prove the command's footprint, so we never auto-approve it.
_SCOPED_COMMAND_ACTION_TYPES = frozenset({"read", "listFiles", "search"})

# Method (wire) → human-readable tool_name we expose in PendingRequest.
# The tool_name is what the frontend dispatches on (in a later PR) to pick
# the right body component. Keeping it short and hyphen-free.
# The elicitation entry is refined per-request by resolve_tool_name() (its
# sub-kind depends on the params, not the method).
APPROVAL_METHODS: dict[str, str] = {
    "item/commandExecution/requestApproval": "commandExecution",
    "item/fileChange/requestApproval":       "fileChange",
    "item/permissions/requestApproval":      "permissions",
    "mcpServer/elicitation/request":         "elicitationForm",
    "item/tool/requestUserInput":            "toolRequestUserInput",
}

ELICITATION_METHOD = "mcpServer/elicitation/request"
REQUEST_USER_INPUT_METHOD = "item/tool/requestUserInput"

# ``_meta`` discriminator marking a form elicitation as an MCP tool-call
# approval (constants from codex-rs ``protocol/src/mcp_approval_meta.rs``).
_APPROVAL_KIND_KEY = "codex_approval_kind"
_APPROVAL_KIND_MCP_TOOL_CALL = "mcp_tool_call"

# tool_names surfaced as questions rather than approvals — drives
# ``PendingRequest.request_type`` and thus the frontend form header.
_QUESTION_TOOL_NAMES = frozenset({
    "elicitationForm", "elicitationUrl", "toolRequestUserInput",
})

# The only methods whose filesystem footprint can be enumerated for the
# work-dir auto-approval. ``permissions`` is a privilege escalation with no
# path footprint; elicitations / user-input forms have none either.
_PATH_AUTO_APPROVABLE_METHODS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
})


def is_approval_method(method: str) -> bool:
    """Return True if ``method`` is one of the 5 approval RPCs we own."""
    return method in APPROVAL_METHODS


def resolve_tool_name(method: str, params: dict | None) -> str:
    """Refine the wire method into the tool_name the frontend dispatches on.

    Static for every method except the MCP elicitation, whose sub-kind lives
    in the params: ``mode=url`` → ``elicitationUrl``; ``mode=form`` tagged
    ``_meta.codex_approval_kind == "mcp_tool_call"`` → ``mcpToolCall`` (Codex
    asking to approve an MCP tool call); any other form → ``elicitationForm``
    (a genuine MCP-server-initiated form). Unknown/missing params fall back to
    the generic form so a schema drift degrades to a visible (if plain)
    prompt, never a silent drop.
    """
    base = APPROVAL_METHODS[method]
    if method != ELICITATION_METHOD:
        return base
    if not isinstance(params, dict):
        return "elicitationForm"
    if params.get("mode") == "url":
        return "elicitationUrl"
    meta = params.get("_meta")
    if isinstance(meta, dict) and meta.get(_APPROVAL_KIND_KEY) == _APPROVAL_KIND_MCP_TOOL_CALL:
        return "mcpToolCall"
    return "elicitationForm"


def derive_request_id(params: dict | None) -> str:
    """Build a stable key to route the user response back to the right future.

    Codex sometimes fans a single ``itemId`` (e.g. an ``ExecCommandBegin``)
    into several sub-exec approvals, each carrying its own ``approvalId``
    (verified in the schema description, ``ServerRequest.json:345-442``).
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

    ``request_type`` is ``tool_approval`` for the 3 legacy methods and the
    MCP-tool-call elicitation sub-kind, and ``ask_user_question`` for the
    other elicitation sub-kinds and ``requestUserInput`` — see
    :data:`_QUESTION_TOOL_NAMES`.

    Raises:
        ValueError: if ``method`` is not one of the Codex approval methods
            (caller must gate with :func:`is_approval_method` first).
    """
    if method not in APPROVAL_METHODS:
        raise ValueError(f"Not a Codex approval method: {method!r}")
    tool_name = resolve_tool_name(method, params)
    request_type = (
        "ask_user_question" if tool_name in _QUESTION_TOOL_NAMES else "tool_approval"
    )
    return PendingRequest(
        request_id=derive_request_id(params),
        request_type=request_type,
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

    Returns a freshly-built dict valid for the requested ``method``:
    - command / file:      ``{"decision": "decline"}``
    - permissions:         ``{"permissions": {}, "scope": "turn"}``
    - elicitation:          ``{"action": "cancel"}``
    - requestUserInput:    ``{"answers": {}}``

    Raises:
        ValueError: if ``method`` is not one of the Codex approval methods
            (caller must gate with :func:`is_approval_method` first).
    """
    if method not in APPROVAL_METHODS:
        raise ValueError(f"Not a Codex approval method: {method!r}")
    if method == "item/permissions/requestApproval":
        # Wire shape per spec ``§1.1.c``: empty granted profile (``{}``)
        # accords zero permissions; ``scope="turn"`` limits the (non-)
        # grant to this turn only. Built inline on every call so callers
        # can mutate freely without leaking state across invocations.
        return {"permissions": {}, "scope": "turn"}
    if method == ELICITATION_METHOD:
        # ``cancel`` (not decline): a teardown is not a user decision. For an
        # MCP tool-call approval Codex skips the call with "user cancelled";
        # for a genuine elicitation ``cancel`` is the MCP "dismissed" action.
        return {"action": "cancel"}
    if method == REQUEST_USER_INPUT_METHOD:
        # An empty answers map is a valid ToolRequestUserInputResponse; the
        # MCP-approval parser maps a missing answer to Cancel.
        return {"answers": {}}
    # Wire shape per spec ``§1.1.{a,b}``. We send ``decline`` rather than
    # ``cancel`` so the turn stays alive — the model can recover or pick
    # another approach instead of being aborted whole-cloth.
    return {"decision": "decline"}


def auto_approve_response_for(method: str) -> dict:
    """Wire response approving an approval that targets only system work dirs.

    command / file: ``{"decision": "accept"}`` (this turn only; not persisted).
    Only ``commandExecution`` and ``fileChange`` are path-auto-approvable
    (see :data:`_PATH_AUTO_APPROVABLE_METHODS`): ``permissions`` is a
    privilege escalation with no path footprint, and elicitations /
    requestUserInput forms have none either — asking for their response is
    a caller bug.

    Raises:
        ValueError: if ``method`` is not a command/file approval method.
    """
    if method not in _PATH_AUTO_APPROVABLE_METHODS:
        raise ValueError(f"Not a path-auto-approvable Codex method: {method!r}")
    return {"decision": "accept"}


def extract_codex_approval_paths(
    method: str, enriched_params: dict | None,
) -> tuple[list[str], bool]:
    """Return ``(candidate_abs_paths, fully_known)`` for a Codex approval.

    ``fully_known=False`` means the request's full filesystem footprint cannot
    be enumerated, so the caller must NOT auto-approve. Only two shapes are
    enumerable:

    - ``fileChange`` (ApplyPatch): every ``changes[].path`` from the streamed
      item payload (spliced under ``_item_payload`` by the agent). The
      ``apply_patch``-run-through-the-shell variant carries a commandExecution
      payload instead (``command``/``commandActions``/``cwd``) — handled too.
    - ``commandExecution``: only when every parsed ``commandActions`` entry is a
      scoped read/listFiles/search with a concrete path (no ``unknown`` action,
      no missing path). Network approvals (``command is None``) are not path
      based and are never auto-approvable.

    ``permissions`` and anything unexpected (incl. the elicitation and
    ``requestUserInput`` methods, which have no filesystem footprint) →
    ``([], False)``.
    """
    if not enriched_params:
        return [], False
    if method == "item/fileChange/requestApproval":
        return _extract_file_change_paths(enriched_params)
    if method == "item/commandExecution/requestApproval":
        return _extract_command_paths(enriched_params)
    return [], False


def _extract_file_change_paths(params: dict) -> tuple[list[str], bool]:
    payload = params.get("_item_payload")
    if not isinstance(payload, dict):
        # No streamed diff to vouch for → cannot enumerate the footprint.
        return [], False
    changes = payload.get("changes")
    if isinstance(changes, list):
        if not changes:
            return [], False
        paths: list[str] = []
        for change in changes:
            if not isinstance(change, dict):
                return [], False
            path = change.get("path")
            if not isinstance(path, str) or not path:
                return [], False
            paths.append(path)
        return paths, True
    # ``apply_patch`` invoked through the shell: the correlated item is a
    # commandExecution (command/commandActions/cwd), not a ``changes`` list.
    if "commandActions" in payload or "command" in payload:
        return _extract_command_paths(payload)
    return [], False


def _extract_command_paths(source: dict) -> tuple[list[str], bool]:
    # ``source`` is either the commandExecution approval params or an
    # apply_patch-via-shell item payload; both carry command/commandActions/cwd.
    if source.get("command") is None:
        # Pure network approval — no command, no path footprint.
        return [], False
    actions = source.get("commandActions")
    if not isinstance(actions, list) or not actions:
        # No parsed actions → we cannot prove what the command touches.
        return [], False
    cwd = source.get("cwd")
    paths: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            return [], False
        if action.get("type") not in _SCOPED_COMMAND_ACTION_TYPES:
            # ``unknown`` (incl. any write) or unclassified → bail out whole.
            return [], False
        path = action.get("path")
        if not isinstance(path, str) or not path:
            return [], False
        paths.append(_resolve_against_cwd(path, cwd))
    return paths, True


def _resolve_against_cwd(path: str, cwd: str | None) -> str:
    """Make a (possibly relative) commandAction path absolute against its
    command's ``cwd`` — NOT the backend process cwd. Left as-is if already
    absolute or if no cwd is available (a relative path with no cwd lands
    out of scope, which is the safe outcome)."""
    if os.path.isabs(path) or not isinstance(cwd, str) or not cwd:
        return path
    return os.path.join(cwd, path)
