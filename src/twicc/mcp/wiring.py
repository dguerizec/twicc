"""Per-session Claude MCP config file (.mcp.json shape).

A FILE, never inline JSON: both the SDK (dict form) and the hybrid CLI would
put the config — token included — on the ``claude`` argv, visible in ``ps``.
Rewritten on every (re)launch: the URL follows the current port and the token
is deterministic, so a stale file self-heals at next start.
"""

from __future__ import annotations

import os
from pathlib import Path

import orjson

from twicc.mcp import mcp_base_url
from twicc.mcp.identity import mint_session_token
from twicc.paths import get_data_dir


def write_claude_mcp_config(session_id: str) -> Path:
    """Write ``<data_dir>/mcp-configs/<session_id>.json`` (0600); return the path."""
    directory = get_data_dir() / "mcp-configs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    config = {
        "mcpServers": {
            "twicc": {
                "type": "http",
                "url": mcp_base_url(),
                "headers": {"Authorization": f"Bearer {mint_session_token(session_id)}"},
            }
        }
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(config))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path
