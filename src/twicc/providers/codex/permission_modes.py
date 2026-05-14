"""
Map the user-facing ``Session.permission_mode`` preset (single string) to the
``(SandboxMode, AskForApproval)`` couple that the Codex SDK expects at
``thread_start`` / ``thread_resume``.

The four modes here are intentionally the same set the frontend exposes today
(``frontend/src/providers/codex/constants.js``). The 5th mode ``strict`` is
added in a later PR.

Wire / preset table (kept in sync with the spec ``§4 Étape 7``):

+-------------+-------------------+--------------------+-----------+----------------+
| Mode (wire) | sandbox_mode      | approval_policy    | Prompts?  | Can write?     |
+=============+===================+====================+===========+================+
| read_only   | read-only         | on-request         | yes       | no             |
| auto        | workspace-write   | on-request         | yes       | workspace only |
| autonomous  | workspace-write   | never              | no        | workspace only |
| yolo        | danger-full-access| never              | no        | anywhere       |
+-------------+-------------------+--------------------+-----------+----------------+

``DEFAULT_MODE`` is the value used when ``Session.permission_mode`` is unset
(``None``) or unknown. In PR2a we ship ``"yolo"`` to preserve the current
behaviour: every Codex session that doesn't have an explicit mode keeps
running with the bypass. PR2b flips this to ``"auto"`` once the frontend
banner is wired.
"""

from __future__ import annotations

from codex_app_server import AskForApproval, SandboxMode

# Preset wire value → (SandboxMode enum, AskForApproval enum)
#
# AskForApproval is a Pydantic RootModel union; the wire strings live in
# ``codex_app_server.generated.v2_all``. We use AskForApproval(value) for
# the explicit string variants ("on-request", "never") — that constructor
# accepts a raw string and round-trips through validation.
_PRESET_MAP: dict[str, tuple[SandboxMode, AskForApproval]] = {
    "read_only":  (SandboxMode.read_only,           AskForApproval("on-request")),
    "auto":       (SandboxMode.workspace_write,     AskForApproval("on-request")),
    "autonomous": (SandboxMode.workspace_write,     AskForApproval("never")),
    "yolo":       (SandboxMode.danger_full_access,  AskForApproval("never")),
}

# PR2a ships this as ``"yolo"`` so existing Codex sessions keep behaving like
# the current bypass. PR2b flips it to ``"auto"`` (workspace-write +
# on-request).
DEFAULT_MODE = "yolo"


def resolve_codex_policy(mode: str | None) -> tuple[SandboxMode, AskForApproval]:
    """Return the ``(sandbox, approval_policy)`` for a preset.

    Unknown / missing mode falls back to ``DEFAULT_MODE``. The two callers
    that matter are :meth:`CodexAgentManager._create_agent` (thread_start /
    thread_resume) and any future place that needs to query the active
    policy for telemetry.
    """
    return _PRESET_MAP.get(mode or DEFAULT_MODE, _PRESET_MAP[DEFAULT_MODE])
