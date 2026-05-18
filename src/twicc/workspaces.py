"""
Read/write workspaces from/to workspaces.json in the data directory.

Workspaces group projects into named collections with optional layout and
filter configuration. They are stored as a simple JSON object in
<data_dir>/workspaces.json.

Follow the exact same pattern as synced_settings.py.
"""

import asyncio
import logging
import os
import re
import tempfile

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.paths import get_workspaces_path

logger = logging.getLogger(__name__)


# Serializes the read-modify-write cycle on workspaces.json across the main
# process: ``auto_add_project_to_workspaces`` (watcher, views, …) and
# ``_handle_update_workspaces`` (WS handler that writes whole-blob updates
# from the UI) must not interleave their reads and writes, or one side's
# changes get clobbered.
_workspaces_lock = asyncio.Lock()


def read_workspaces() -> dict:
    """Read workspaces from workspaces.json.

    Returns an empty dict if the file doesn't exist or is invalid.
    """
    path = get_workspaces_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}


def write_workspaces(data: dict) -> None:
    """Write workspaces to workspaces.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_workspaces_path()
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def match_pattern(directory: str, pattern: str) -> bool:
    """Check if a directory path matches a pattern using ``*`` as wildcard.

    If the pattern contains no ``*``, it is treated as a directory prefix
    (``/some/path`` behaves like ``/some/path/*``).
    """
    effective = pattern if "*" in pattern else pattern.rstrip("/") + "/*"
    regex = re.compile("^" + ".*".join(re.escape(part) for part in effective.split("*")) + "$", re.IGNORECASE)
    return regex.search(directory) is not None


async def auto_add_project_to_workspaces(project_id: str, directory: str) -> None:
    """Auto-add a newly detected project to workspaces whose patterns match
    its directory.

    The read-modify-write on ``workspaces.json`` runs inside
    ``_workspaces_lock`` so a concurrent ``_handle_update_workspaces``
    (whole-blob writes from the UI) can't clobber the appended project_id
    (and vice versa). If at least one workspace was modified, broadcasts
    ``workspaces_updated`` outside the lock.

    Idempotent: a workspace that already lists the project is skipped, no
    write, no broadcast.
    """
    def _read_modify_write() -> list[dict] | None:
        data = read_workspaces()
        workspaces = data.get("workspaces", [])
        modified = False
        for ws in workspaces:
            patterns = ws.get("autoProjectPatterns", [])
            if not patterns or project_id in ws.get("projectIds", []):
                continue
            if any(match_pattern(directory, p) for p in patterns):
                ws.setdefault("projectIds", []).append(project_id)
                modified = True
                logger.info("Auto-added project %s to workspace %r",
                            project_id, ws.get("name", ws.get("id")))
        if not modified:
            return None
        write_workspaces(data)
        return workspaces

    async with _workspaces_lock:
        workspaces = await sync_to_async(_read_modify_write)()
    if workspaces is None:
        return

    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "workspaces_updated", "workspaces": workspaces},
    })
