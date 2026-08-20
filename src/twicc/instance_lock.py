"""
Cross-platform exclusive lock to ensure only one TwiCC instance runs per data directory.

The lock is a kernel-managed advisory file lock on ``<data_dir>/twicc.lock``
(POSIX ``flock`` on Linux/macOS, ``LockFileEx`` via ``msvcrt.locking`` on
Windows). It is released automatically by the kernel when the holding
process dies — including ``SIGKILL`` and crashes — so there are no stale
locks to clean up.

A sidecar ``<data_dir>/twicc.info.json`` file carries the holder's PID,
port, and start time. It is purely informational: the exclusivity
guarantee comes from the lockfile alone. The info file is read only when
rejecting a second start, to render a helpful error message; it is
removed on graceful shutdown and is allowed to be stale (PID-recycled
or left over from a crash) without affecting correctness.

Scope: one lock per ``data_dir``. Two instances on different data
directories (e.g. main install + worktree) coexist fine.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, UTC
from pathlib import Path

import orjson

logger = logging.getLogger(__name__)

LOCK_FILENAME = "twicc.lock"
INFO_FILENAME = "twicc.info.json"


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            # EAGAIN/EWOULDBLOCK — lock is held by another process. Any other
            # OSError (ENOLCK on filesystems that don't support flock, etc.)
            # propagates so the user sees an actionable error rather than a
            # silent "TwiCC is already running" false positive on first launch.
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class InstanceAlreadyRunning(RuntimeError):
    """Raised when another TwiCC instance already holds the lock on this data directory."""

    def __init__(self, data_dir: Path, info: dict | None) -> None:
        self.data_dir = data_dir
        self.info = info
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = [
            "Another TwiCC instance is already running on this data directory:",
            f"  {self.data_dir}",
        ]
        if self.info:
            details = []
            pid = self.info.get("pid")
            if pid is not None:
                details.append(f"PID {pid}")
            port = self.info.get("port")
            if port is not None:
                details.append(f"port {port}")
            started_at = self.info.get("started_at")
            if started_at is not None:
                details.append(f"started at {started_at}")
            if details:
                lines.append("Holder: " + ", ".join(details))
        lines.append("")
        lines.append(
            "If you are sure no other TwiCC is running, clean up any leftover "
            "process with `pkill -f twicc` and try again."
        )
        return "\n".join(lines)


class InstanceLock:
    """Context manager owning the exclusive flock on ``<data_dir>/twicc.lock``."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock_path = data_dir / LOCK_FILENAME
        self._info_path = data_dir / INFO_FILENAME
        self._fd: int | None = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def acquire(self) -> None:
        """Try to acquire the lock; raise InstanceAlreadyRunning if another instance holds it."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            raise InstanceAlreadyRunning(self._data_dir, self._read_info())
        self._fd = fd

    def release(self) -> None:
        """Release the lock and remove the sidecar info file. Idempotent."""
        if self._fd is None:
            return
        _unlock(self._fd)
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        # The lockfile itself stays around as the flock anchor for the next
        # start — only the info file is removed.
        try:
            self._info_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Could not remove %s: %s", self._info_path, exc)

    def write_info(self, *, port: int | None = None) -> None:
        """Write the sidecar info file used to render a helpful 'already running' message."""
        info: dict[str, object] = {
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if port is not None:
            info["port"] = port
        try:
            self._info_path.write_bytes(orjson.dumps(info, option=orjson.OPT_INDENT_2))
        except OSError as exc:
            logger.debug("Could not write %s: %s", self._info_path, exc)

    def _read_info(self) -> dict | None:
        try:
            return orjson.loads(self._info_path.read_bytes())
        except (FileNotFoundError, ValueError, OSError):
            return None
