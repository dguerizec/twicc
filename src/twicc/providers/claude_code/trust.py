"""Read/write the Claude Code per-project trust flag in ``~/.claude.json``.

Read = the project's **exact** entry only (no walk-up): seeding adopts a genuine
own decision, never an inherited one. Write = atomic read-modify-write that
touches only ``hasTrustDialogAccepted`` for the given directory and preserves
every other key, under an advisory sidecar lock. See
docs/plans/2026-06-09-project-trust-design.md §7.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

import orjson

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    return Path.home() / ".claude.json"


def read_trust(directory: str) -> bool | None:
    """Return ``hasTrustDialogAccepted`` for the EXACT *directory*, else None.

    None means "no own decision recorded" (absent entry, missing/corrupt file,
    or a non-boolean value). We never walk up parents here — that is the CLI's
    runtime behaviour, not a decision *for this directory*.
    """
    path = _config_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (orjson.JSONDecodeError, ValueError, OSError):
        logger.warning("Could not read %s for trust seeding", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    entry = (data.get("projects") or {}).get(directory)
    if not isinstance(entry, dict):
        return None
    value = entry.get("hasTrustDialogAccepted")
    return value if isinstance(value, bool) else None


def write_trust(directory: str, trusted: bool) -> None:
    """Set ``projects[directory].hasTrustDialogAccepted`` to *trusted*.

    Atomic (temp + ``os.replace``) under an advisory sidecar lock, re-reading the
    file *inside* the lock to minimize the lost-update window versus the Claude
    CLI. Preserves every other key. Refuses to write (rather than clobber) a file
    it cannot parse as a JSON object. Idempotent — a no-op when already correct.
    """
    path = _config_path()
    lock_path = path.with_name(path.name + ".twicc.lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            raw = path.read_bytes()
            data = orjson.loads(raw) if raw.strip() else {}
        except FileNotFoundError:
            data = {}
        except (orjson.JSONDecodeError, ValueError) as exc:
            logger.error("Refusing to write %s: not valid JSON (%s)", path, exc)
            return
        if not isinstance(data, dict):
            logger.error("Refusing to write %s: top-level is not an object", path)
            return
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            logger.error("Refusing to write %s: 'projects' is not an object", path)
            return
        entry = projects.get(directory)
        if not isinstance(entry, dict):
            entry = projects[directory] = {}
        if entry.get("hasTrustDialogAccepted") is trusted:
            return  # idempotent
        entry["hasTrustDialogAccepted"] = trusted
        tmp = path.with_name(f"{path.name}.twicc.tmp.{os.getpid()}")
        try:
            tmp.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            os.replace(str(tmp), str(path))
        finally:
            tmp.unlink(missing_ok=True)
        logger.info("Set Claude trust for %s -> %s", directory, trusted)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
