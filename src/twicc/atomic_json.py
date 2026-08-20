"""Cross-process-safe atomic writes for the singleton JSON config files.

Two primitives, both generalising the pattern proven in
:mod:`twicc.providers.claude_code.trust`:

- :func:`atomic_write_json` — write a JSON value to a file atomically (unique
  temp via :func:`tempfile.mkstemp` + :func:`os.replace`). A partial file is
  never observed and concurrent writers never collide on the temp name. Use it
  for *whole-blob* overwrites, where the caller already holds the full new
  state (last-write-wins is inherent and a lock would change nothing).

- :func:`locked_json_file` — a context manager holding an advisory
  cross-process lock (:func:`fcntl.flock` on a sidecar ``<name>.lock``) for a
  whole *read-modify-write* cycle. The file is re-read **inside** the lock and
  written through :func:`atomic_write_json`, so an append/patch can't be lost
  to a concurrent writer — including the ``twicc`` CLI running in another
  process. A file that exists but does not parse as a JSON object is refused
  (never overwritten), so recoverable data is not destroyed under a writer.

``flock`` acquisition blocks the calling thread, so a caller running on the
asyncio event loop MUST go through ``sync_to_async`` (the CLI runs
synchronously). The lock is advisory and only honoured by writers that take it
here; readers never lock (the atomic ``os.replace`` already guarantees they see
a complete file, the old one or the new one, never a partial mix).
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

import orjson

logger = logging.getLogger(__name__)


class CorruptConfigError(Exception):
    """A config file exists but is not a valid JSON object; refused for write."""


def atomic_write_json(path: Path, data: object) -> None:
    """Serialise *data* to *path* atomically (unique temp + ``os.replace``).

    The temp is created by :func:`tempfile.mkstemp` (unique per call, mode
    ``0o600``) in the destination directory, so the rename is atomic on the
    same filesystem and concurrent writers never share a temp name. On any
    failure the temp is removed and the original *path* is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class JsonFileTxn:
    """Handle yielded by :func:`locked_json_file`.

    Mutate :attr:`data` (the parsed file contents), then call :meth:`write` to
    persist. Skipping :meth:`write` leaves the file untouched — the idempotent
    / no-op path.
    """

    __slots__ = ("_path", "data", "written")

    def __init__(self, path: Path, data: dict) -> None:
        self._path = path
        self.data: dict = data
        self.written = False

    def write(self, new_data: object | None = None) -> None:
        """Persist *new_data* (defaults to the mutated :attr:`data`) atomically."""
        atomic_write_json(self._path, self.data if new_data is None else new_data)
        self.written = True


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold the advisory cross-process lock for *path* (sidecar ``<name>.lock``).

    Used on its own to serialise a *whole-blob* overwrite with the
    read-modify-write callers of the same file (which take the same lock via
    :func:`locked_json_file`), and internally by :func:`locked_json_file`.
    Blocks the calling thread — never acquire it directly on the event loop.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


@contextmanager
def locked_json_file(path: Path, *, default: dict) -> Iterator[JsonFileTxn]:
    """Hold a cross-process lock for a read-modify-write of *path*.

    Acquires the :func:`file_lock` on a sidecar ``<name>.lock`` file, reads and
    parses *path* inside the lock (a deep copy of *default* when the file is
    absent or empty), and yields a :class:`JsonFileTxn`. The lock is held until
    the ``with`` block exits, serialising the whole cycle against other threads
    *and* other processes.

    Raises :class:`CorruptConfigError` (without writing) when *path* exists but
    is not a JSON object.
    """
    path = Path(path)
    with file_lock(path):
        data = _read_json_object(path, default)
        yield JsonFileTxn(path, data)


def _read_json_object(path: Path, default: dict) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return orjson.loads(orjson.dumps(default))
    if not raw.strip():
        return orjson.loads(orjson.dumps(default))
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise CorruptConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CorruptConfigError(f"{path}: top-level is not a JSON object")
    return data
