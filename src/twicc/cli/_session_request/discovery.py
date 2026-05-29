"""Discovery helpers for the CLI: data dir + heartbeat check."""

from __future__ import annotations

import time
from pathlib import Path

from twicc.heartbeat import HEARTBEAT_FILENAME, HEARTBEAT_STALE_AFTER_SECONDS


class ServerDownError(Exception):
    """Raised when the heartbeat is missing or stale."""


def get_data_dir() -> Path:
    """Return the TwiCC data directory.

    Thin wrapper around :func:`twicc.paths.get_data_dir` so the CLI
    package doesn't reach into the wider twicc code apart from the
    well-defined helpers.
    """
    from twicc.paths import get_data_dir as _get_data_dir
    return _get_data_dir()


def check_heartbeat(data_dir: Path | None = None) -> float:
    """Verify the server's heartbeat file is fresh.

    Returns the age in seconds (for telemetry / output). Raises
    :class:`ServerDownError` if the file is missing or stale.
    """
    if data_dir is None:
        data_dir = get_data_dir()
    path = data_dir / HEARTBEAT_FILENAME
    if not path.exists():
        raise ServerDownError(
            "TwiCC server does not appear to be running "
            "(or is still starting up). Run `twicc` in another terminal "
            "and wait until it is ready."
        )
    age = time.time() - path.stat().st_mtime
    if age > HEARTBEAT_STALE_AFTER_SECONDS:
        raise ServerDownError(
            f"TwiCC server is unresponsive (last heartbeat {int(age)}s ago). "
            f"Make sure it is still running."
        )
    return age
