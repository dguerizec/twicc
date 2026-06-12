"""GUI answers for hybrid pending requests — wire conversion + status files.

The hybrid agent resolves a pending request by writing the user's decision
as a ``<drop stem>.status.json`` file next to the hook's drop file (the
drop-requests convention); the hook polls it, prints it on stdout, and the
CLI applies the decision — dismissing the TUI dialog (design doc 2026-06-12
§4.1/§4.4, all verified empirically).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import orjson
from claude_agent_sdk import PermissionResultAllow, PermissionUpdate

from twicc.paths import get_hybrid_hooks_dir

logger = logging.getLogger(__name__)

HOOK_EVENT = "PermissionRequest"

# The CLI provably ignores hook output for an already-resolved prompt, so a
# dummy decision doubles as an orphan-hook reaper: the still-polling hook
# consumes it and exits instead of polling until the CLI-side hook timeout.
DUMMY_REAP_STATUS = {
    "hookSpecificOutput": {
        "hookEventName": HOOK_EVENT,
        "decision": {"behavior": "deny", "message": "Already answered in the terminal"},
    },
}


def drop_path_for(session_id: str, nonce: str) -> Path:
    """Path of the hook's drop file for one PermissionRequest invocation."""
    return get_hybrid_hooks_dir() / f"{session_id}__{HOOK_EVENT}__{nonce}.json"


def status_path_for(session_id: str, nonce: str) -> Path:
    """Path of the answer file the hook polls for (drop stem + .status.json)."""
    return get_hybrid_hooks_dir() / f"{session_id}__{HOOK_EVENT}__{nonce}.status.json"


def to_hook_output(response) -> dict:
    """Convert the SDK-typed answer built by ``ws.py`` into hook stdout JSON.

    ``PermissionResultAllow.updated_permissions`` carries ``PermissionUpdate``
    dataclasses (ws.py reconstructs them from the frontend dicts) — serialize
    them back for the wire. Verified decisions schema: ``behavior`` allow/deny,
    ``updatedInput`` (AskUserQuestion answers ride here), ``updatedPermissions``
    (e.g. the ``setMode acceptEdits`` suggestion), ``message`` on deny.
    """
    if isinstance(response, PermissionResultAllow):
        decision: dict = {"behavior": "allow"}
        if response.updated_input is not None:
            decision["updatedInput"] = response.updated_input
        if response.updated_permissions is not None:
            decision["updatedPermissions"] = [
                p.to_dict() if isinstance(p, PermissionUpdate) else p
                for p in response.updated_permissions
            ]
    else:
        decision = {"behavior": "deny", "message": getattr(response, "message", "Denied")}
    return {"hookSpecificOutput": {"hookEventName": HOOK_EVENT, "decision": decision}}


def write_status(path: Path, data: dict) -> None:
    """Atomic status write (same recipe as the drop-requests watcher)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(orjson.dumps(data))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
