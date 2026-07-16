"""
Map the user-facing ``Session.permission_mode`` preset (single string) to the
``(SandboxMode, AskForApproval, ApprovalsReviewer)`` policy that the Codex SDK
expects at ``thread_start`` / ``thread_resume``.

Wire / preset table (kept in sync with the spec ``§4 Étape 7``):

+-------------+-------------------+-----------------+-------------+----------------+
| Mode (wire) | sandbox_mode      | approval_policy | reviewer    | Escalations    |
+=============+===================+=================+=============+================+
| read_only   | read-only         | on-request      | user        | ask user       |
| strict      | read-only         | never           | user        | refuse         |
| auto        | workspace-write   | on-request      | user        | ask user       |
| autonomous  | workspace-write   | never           | user        | refuse         |
| auto_review | workspace-write   | on-request      | auto_review | review         |
| yolo        | danger-full-access| granular        | user        | unrestricted   |
+-------------+-------------------+-----------------+-------------+----------------+

``yolo`` uses a ``granular`` approval policy (not ``never``) so MCP servers can
still elicit input from the user — see ``_YOLO_APPROVAL`` below for the full
rationale. Command autonomy is unchanged; only MCP elicitations reach the user.

``DEFAULT_MODE`` is the value used when ``Session.permission_mode`` is unset
(``None``) or unknown. Since PR2b it is ``"auto"`` — ``workspace-write`` +
``on-request``. Users can opt into a more permissive mode (``"autonomous"``
to skip prompts, ``"yolo"`` for full unrestricted access) or a stricter one
(``"read_only"``) via the session settings picker.
"""

from __future__ import annotations

from typing import NamedTuple

from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    DangerFullAccessSandboxPolicy,
    Granular,
    GranularAskForApproval,
    ReadOnlySandboxPolicy,
    SandboxMode,
    SandboxPolicy,
    WorkspaceWriteSandboxPolicy,
)


class CodexPermissionPolicy(NamedTuple):
    """Resolved thread-level permission controls for one user-facing preset."""

    sandbox_mode: SandboxMode
    approval_policy: AskForApproval
    approvals_reviewer: ApprovalsReviewer


class CodexTurnOverrides(NamedTuple):
    """Per-turn form of :class:`CodexPermissionPolicy`."""

    sandbox_policy: SandboxPolicy
    approval_policy: AskForApproval
    approvals_reviewer: ApprovalsReviewer


# YOLO's approval policy: full autonomy that STILL forwards MCP elicitations to
# the user. A plain ``never`` makes Codex auto-resolve every elicitation before
# it reaches the client (decline for schemas with fields, accept-empty for
# field-less confirms) — so an MCP server that needs input can never ask. A
# ``granular`` policy with ``mcp_elicitations=true`` is the only variant that
# both keeps autonomy and lets the elicitation through
# (``elicitation_is_rejected_by_policy`` returns false for it).
#
# Command autonomy is preserved because YOLO's sandbox is ``danger-full-access``
# (an *unrestricted* filesystem): Codex's ``default_exec_approval_requirement``
# only raises an exec approval under ``granular`` when the filesystem is
# ``Restricted``, so with full access commands run without a prompt exactly like
# ``never`` — the other granular flags never fire and are left False (auto-reject
# any stray prompt, matching YOLO's no-interruption intent). This is why the
# trick works ONLY for ``yolo``: ``autonomous`` uses ``workspace-write`` (a
# Restricted filesystem), where a granular policy would auto-reject
# out-of-workspace commands instead of sandbox-failing them, so it stays
# ``never`` (elicitations auto-resolve there — consistent with a non-interactive
# autonomous mode).
_YOLO_APPROVAL = AskForApproval(
    GranularAskForApproval(
        granular=Granular(
            mcp_elicitations=True,
            sandbox_approval=False,
            rules=False,
            request_permissions=False,
            skill_approval=False,
        )
    )
)

# Preset wire value → sandbox, approval policy, and reviewer.
#
# AskForApproval is a Pydantic RootModel union; the wire strings live in
# ``openai_codex.generated.v2_all``. We use AskForApproval(value) for
# the explicit string variants ("on-request", "never") — that constructor
# accepts a raw string and round-trips through validation — and the
# ``_YOLO_APPROVAL`` granular object for ``yolo`` (see above).
_PRESET_MAP: dict[str, CodexPermissionPolicy] = {
    "read_only": CodexPermissionPolicy(
        SandboxMode.read_only, AskForApproval("on-request"), ApprovalsReviewer.user,
    ),
    "strict": CodexPermissionPolicy(
        SandboxMode.read_only, AskForApproval("never"), ApprovalsReviewer.user,
    ),
    "auto": CodexPermissionPolicy(
        SandboxMode.workspace_write, AskForApproval("on-request"), ApprovalsReviewer.user,
    ),
    "autonomous": CodexPermissionPolicy(
        SandboxMode.workspace_write, AskForApproval("never"), ApprovalsReviewer.user,
    ),
    "auto_review": CodexPermissionPolicy(
        SandboxMode.workspace_write, AskForApproval("on-request"), ApprovalsReviewer.auto_review,
    ),
    "yolo": CodexPermissionPolicy(
        SandboxMode.danger_full_access, _YOLO_APPROVAL, ApprovalsReviewer.user,
    ),
}

# ``"auto"`` is the canonical default since PR2b: ``workspace-write`` sandbox
# + ``on-request`` approval policy. Existing sessions with ``permission_mode``
# already stored in the DB keep their stored value; sessions where the field
# is NULL fall on this default. To recover the pre-PR2a unrestricted
# behaviour, pick ``"yolo"`` in the session picker.
DEFAULT_MODE = "auto"


def resolve_codex_policy(mode: str | None) -> CodexPermissionPolicy:
    """Return the sandbox, approval policy, and reviewer for a preset.

    Unknown / missing mode falls back to ``DEFAULT_MODE``. The two callers
    that matter are :meth:`CodexAgentManager._create_agent` (thread_start /
    thread_resume) and any future place that needs to query the active
    policy for telemetry.
    """
    return _PRESET_MAP.get(mode or DEFAULT_MODE, _PRESET_MAP[DEFAULT_MODE])


def _to_sandbox_policy(
    sandbox_mode: SandboxMode,
    writable_roots: list[str] | None = None,
    *,
    network_access: bool = False,
) -> SandboxPolicy:
    """Convert a ``SandboxMode`` enum (used by ``thread_start(sandbox=...)``)
    to a ``SandboxPolicy`` ``RootModel`` (used by ``thread.turn(sandbox_policy=...)``).

    The SDK has two parallel types: ``SandboxMode`` for thread bootstrap,
    ``SandboxPolicy`` for per-turn override. The mapping is mechanical
    because both encode the same 3 modes we use.

    ``writable_roots`` are extra absolute directories the agent may write to on
    top of the workspace (its own artifacts/scratch dirs, plus the orchestration
    root's shared scratch). They only apply to ``workspace-write``: ``read-only``
    already reads the whole filesystem and forbids writes, and
    ``danger-full-access`` writes everywhere — neither type even carries the
    field, so the list is silently irrelevant there.

    ``network_access`` opens the workspace sandbox's network channel. TwiCC
    enables it only for ``auto_review`` turns. Codex's managed network proxy is
    intentionally left off: without an administrator requirements layer, an
    allowlist miss is an opaque HTTP 403 rather than a Guardian decision. The
    flag is ignored by the other sandbox variants.
    """
    if sandbox_mode is SandboxMode.read_only:
        return SandboxPolicy(root=ReadOnlySandboxPolicy(type="readOnly"))
    if sandbox_mode is SandboxMode.workspace_write:
        return SandboxPolicy(root=WorkspaceWriteSandboxPolicy(
            type="workspaceWrite",
            network_access=network_access,
            writable_roots=writable_roots or [],
        ))
    if sandbox_mode is SandboxMode.danger_full_access:
        return SandboxPolicy(root=DangerFullAccessSandboxPolicy(type="dangerFullAccess"))
    # SandboxMode is an enum with exactly the 3 values above; unreachable.
    raise ValueError(f"Unsupported SandboxMode: {sandbox_mode!r}")


def resolve_codex_turn_overrides(
    mode: str | None,
    writable_roots: list[str] | None = None,
) -> CodexTurnOverrides:
    """Return the sandbox, approval policy, and reviewer for a turn override.

    Wraps :func:`resolve_codex_policy` and converts the sandbox to the
    ``RootModel`` shape ``thread.turn`` requires. Use this in
    ``CodexAgent._run_turn`` to translate the live ``agent_settings.permission_mode``
    into the SDK kwargs. ``writable_roots`` (the session's pre-created work
    dirs) are folded into the workspace-write policy; see
    :func:`_to_sandbox_policy`.
    """
    sandbox_mode, approval_policy, approvals_reviewer = resolve_codex_policy(mode)
    return CodexTurnOverrides(
        _to_sandbox_policy(
            sandbox_mode,
            writable_roots,
            network_access=approvals_reviewer is ApprovalsReviewer.auto_review,
        ),
        approval_policy,
        approvals_reviewer,
    )
