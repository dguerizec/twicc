"""
Reader utilities for Claude Code's per-session task files.

The Claude Code CLI maintains a folder ``~/.claude/tasks/<session_id>/``
with one JSON file per task created via ``TaskCreate``. Each file is
named after the task id (``1.json``, ``2.json``, …) and carries the
canonical state of the task (``id``, ``subject``, ``description``,
``activeForm``, ``status``, ``blocks``, ``blockedBy``).

These helpers let the compute pipeline splice the task payload into the
matching ``TaskCreate`` / ``TaskUpdate`` / ``TaskGet`` tool_use items so
the frontend can render a meaningful summary (label, status icon, id /
total count) without an extra round-trip to disk on every paint.

The tool inputs only carry partial information:
  - ``TaskCreate`` carries ``subject`` / ``activeForm`` / ``description``
    but not the assigned id (the CLI assigns it after the call).
  - ``TaskUpdate`` and ``TaskGet`` carry the id directly.

So ``find_task_by_create_input`` matches by ``subject`` + ``activeForm``
to recover the id at TaskCreate time, while the other two read the file
by id directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import orjson

logger = logging.getLogger(__name__)


class TasksReader:
    """Filesystem reader for ``~/.claude/tasks/<session_id>/``."""

    TASKS_DIR: ClassVar[Path] = Path.home() / ".claude" / "tasks"

    @classmethod
    def get_session_dir(cls, session_id: str) -> Path:
        return cls.TASKS_DIR / session_id

    @classmethod
    def read_task_file(cls, session_id: str, task_id: str) -> dict | None:
        """Read a single task JSON file by id. Returns the parsed dict or
        ``None`` when the file is missing or malformed."""
        path = cls.get_session_dir(session_id) / f"{task_id}.json"
        try:
            raw = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None
        except OSError:
            logger.debug("Failed to read task file %s", path, exc_info=True)
            return None
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            logger.debug("Malformed task JSON at %s", path)
            return None

    @classmethod
    def find_task_by_create_input(
        cls, session_id: str, subject: str | None, active_form: str | None,
    ) -> dict | None:
        """Locate the task file matching a TaskCreate input.

        The id isn't in the tool input, so we scan the session's task
        directory for a file whose ``subject`` and ``activeForm`` match
        the call. When multiple files match (duplicate subjects), the
        one with the highest numeric id wins — that is the most recently
        created task and matches the typical sequential-id assignment by
        the CLI.
        """
        if not subject:
            return None
        session_dir = cls.get_session_dir(session_id)
        try:
            entries = list(session_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            logger.debug("Failed to list %s", session_dir, exc_info=True)
            return None

        best: dict | None = None
        best_id_num: int = -1
        for entry in entries:
            if entry.suffix != ".json":
                continue
            try:
                data = orjson.loads(entry.read_bytes())
            except (OSError, orjson.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("subject") != subject:
                continue
            # ``activeForm`` is optional in the CLI but TwiCC's display
            # logic prefers it when present, so demand a match when the
            # call had one.
            if active_form is not None and data.get("activeForm") != active_form:
                continue
            task_id = data.get("id")
            try:
                id_num = int(task_id) if task_id is not None else -1
            except (TypeError, ValueError):
                id_num = -1
            if id_num > best_id_num:
                best = data
                best_id_num = id_num
        return best

    @classmethod
    def count_session_tasks(cls, session_id: str) -> int:
        """Count ``*.json`` files in the session's task directory."""
        session_dir = cls.get_session_dir(session_id)
        try:
            return sum(1 for entry in session_dir.iterdir() if entry.suffix == ".json")
        except (FileNotFoundError, NotADirectoryError):
            return 0
        except OSError:
            logger.debug("Failed to count tasks in %s", session_dir, exc_info=True)
            return 0

    @classmethod
    def read_all_tasks(cls, session_id: str) -> list[dict]:
        """Read every task JSON file in the session's task directory.

        Returned list is sorted by numeric id (the CLI assigns sequential
        ids, so this mirrors creation order). Entries that can't be parsed
        as JSON or that lack a numeric id are silently dropped.
        """
        session_dir = cls.get_session_dir(session_id)
        try:
            entries = list(session_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError:
            logger.debug("Failed to list %s", session_dir, exc_info=True)
            return []

        tasks: list[tuple[int, dict]] = []
        for entry in entries:
            if entry.suffix != ".json":
                continue
            try:
                data = orjson.loads(entry.read_bytes())
            except (OSError, orjson.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            try:
                id_num = int(data.get("id"))
            except (TypeError, ValueError):
                continue
            tasks.append((id_num, data))
        tasks.sort(key=lambda item: item[0])
        return [data for _, data in tasks]
