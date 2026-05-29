"""Atomic drop-file writer."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import orjson


class DropFile(NamedTuple):
    path: Path
    request_uuid: str


def write_drop_file(
    data_dir: Path,
    payload: dict,
    *,
    kind: str = "create",
) -> DropFile:
    """Atomically write the request file. Returns the path and uuid.

    ``kind`` is stamped into the payload so the server-side watcher can route
    the request to the matching service. For ``kind="create"`` (the historical
    default), the freshly minted ``request_uuid`` is also injected as
    ``session_id`` — Claude Code uses it as ``--session-id`` when starting the
    new session. For every other ``kind`` (e.g. ``"send"``), the caller is
    responsible for providing ``session_id`` in the payload itself.
    """
    directory = data_dir / "sessions-pending"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    request_uuid = str(uuid.uuid4())
    final_path = directory / f"{request_uuid}.json"
    tmp_path = directory / f"{request_uuid}.json.tmp"

    full_payload = {**payload, "kind": kind}
    if kind == "create":
        full_payload["session_id"] = request_uuid

    envelope = {
        "version": 1,
        "request_uuid": request_uuid,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitter": {
            "user": os.environ.get("USER", "?"),
            "hostname": os.uname().nodename if hasattr(os, "uname") else "?",
            "pid": os.getpid(),
        },
        "payload": full_payload,
    }

    tmp_path.write_bytes(orjson.dumps(envelope))
    os.replace(tmp_path, final_path)
    try:
        os.chmod(final_path, 0o600)
    except Exception:
        pass

    return DropFile(path=final_path, request_uuid=request_uuid)
