"""CLI implementation for the ``twicc status`` subcommand."""

import sys
import time
from pathlib import Path

import orjson
import psutil


def main() -> None:
    """Report the live TwiCC backend's status as JSON, exit 0 only when fully running.

    Inspects the two sidecar files written by the live backend
    (``twicc.info.json`` + ``.server-heartbeat``) plus the kernel process
    table to classify the state into one of five values: ``running``,
    ``starting``, ``stale``, ``dead_pid``, ``not_running``. Pure read —
    no Django setup, no lock acquisition — so it is safe to call
    concurrently with a live TwiCC.
    """
    from twicc.heartbeat import HEARTBEAT_FILENAME, HEARTBEAT_STALE_AFTER_SECONDS
    from twicc.instance_lock import INFO_FILENAME, LOCK_FILENAME
    from twicc.paths import get_data_dir

    data_dir = get_data_dir()
    lock_path = data_dir / LOCK_FILENAME
    info_path = data_dir / INFO_FILENAME
    heartbeat_path = data_dir / HEARTBEAT_FILENAME

    output: dict[str, object] = {
        "status": "not_running",
        "data_dir": str(data_dir),
        "lock_path": str(lock_path),
        "info_path": str(info_path),
        "heartbeat_path": str(heartbeat_path),
        "pid": None,
        "port": None,
        "started_at": None,
        "heartbeat_age_seconds": None,
        "heartbeat_stale_after_seconds": HEARTBEAT_STALE_AFTER_SECONDS,
    }

    info = _read_info(info_path)
    if info is None:
        _emit_and_exit(output, code=1)

    pid = info.get("pid") if isinstance(info.get("pid"), int) else None
    output["pid"] = pid
    output["port"] = info.get("port") if isinstance(info.get("port"), int) else None
    output["started_at"] = info.get("started_at") if isinstance(info.get("started_at"), str) else None

    if pid is None or not _pid_alive(pid):
        output["status"] = "dead_pid"
        _emit_and_exit(output, code=1)

    age = _heartbeat_age(heartbeat_path)
    if age is None:
        output["status"] = "starting"
        _emit_and_exit(output, code=1)

    output["heartbeat_age_seconds"] = round(age, 1)
    if age > HEARTBEAT_STALE_AFTER_SECONDS:
        output["status"] = "stale"
        _emit_and_exit(output, code=1)

    output["status"] = "running"
    _emit_and_exit(output, code=0)


def _read_info(info_path: Path) -> dict | None:
    try:
        return orjson.loads(info_path.read_bytes())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        # psutil may raise on permission-restricted systems; treat any failure
        # the same as "PID not provably alive" so we never claim "running"
        # against a possibly-recycled PID.
        return False


def _heartbeat_age(heartbeat_path: Path) -> float | None:
    try:
        mtime = heartbeat_path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return None
    return time.time() - mtime


def _emit_and_exit(data: dict, *, code: int) -> None:
    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
    sys.exit(code)
