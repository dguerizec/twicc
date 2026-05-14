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
(``None``) or unknown. Since PR2b it is ``"auto"`` — ``workspace-write`` +
``on-request``. Users can opt into a more permissive mode (``"autonomous"``
to skip prompts, ``"yolo"`` for full unrestricted access) or a stricter one
(``"read_only"``) via the session settings picker.
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

# ``"auto"`` is the canonical default since PR2b: ``workspace-write`` sandbox
# + ``on-request`` approval policy. Existing sessions with ``permission_mode``
# already stored in the DB keep their stored value; sessions where the field
# is NULL fall on this default. To recover the pre-PR2a unrestricted
# behaviour, pick ``"yolo"`` in the session picker.
DEFAULT_MODE = "auto"


def resolve_codex_policy(mode: str | None) -> tuple[SandboxMode, AskForApproval]:
    """Return the ``(sandbox, approval_policy)`` for a preset.

    Unknown / missing mode falls back to ``DEFAULT_MODE``. The two callers
    that matter are :meth:`CodexAgentManager._create_agent` (thread_start /
    thread_resume) and any future place that needs to query the active
    policy for telemetry.
    """
    return _PRESET_MAP.get(mode or DEFAULT_MODE, _PRESET_MAP[DEFAULT_MODE])
