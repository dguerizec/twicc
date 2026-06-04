"""
Codex file watcher.

Thin :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` subclass
that plugs in Codex's directory layout and compute object. Everything else
(watchfiles loop, ORM updates, broadcasts, search indexing, polling) lives
in the base.

Codex uses a date-based layout — ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``
— with no per-project subfolder, so we cannot derive ``project_id`` from
the path itself. :meth:`CodexSessionsWatcher.parse_session_file` instead
reads the first JSONL line (the ``session_meta`` event) to recover the
session UUID and the working directory, then maps that ``cwd`` to the
canonical TwiCC project id via :func:`twicc.paths.path_to_project_id` —
matching exactly what :func:`twicc.providers.codex.initial_sync.sync_all`
does for brand-new files. The same first-line read also surfaces a
parent session id when ``payload.source.subagent.thread_spawn`` is set,
so we can mark the file as a :class:`SessionType.SUBAGENT` and let the
base watcher wire it under its parent.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from twicc.core.models import SessionType
from twicc.paths import path_to_project_id
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    ParsedSessionFile,
)

from .compute import get_compute as _get_compute
from .helpers import CodexHelpers
from .initial_sync import extract_session_meta, is_session_file

logger = logging.getLogger(__name__)


class CodexSessionsWatcher(BaseSessionsWatcher):
    """File watcher for Codex's ``~/.codex/sessions/`` layout.

    Recognized layout: ``YYYY/MM/DD/rollout-<uuid>.jsonl`` →
    :class:`SessionType.SESSION` or :class:`SessionType.SUBAGENT` when the
    first-line ``session_meta`` carries a
    ``payload.source.subagent.thread_spawn.parent_thread_id``. Subagents
    use the exact same on-disk layout as top-level sessions; they're
    discriminated solely by that field.
    """

    projects_dir: ClassVar[Path] = CodexHelpers.SESSIONS_DIR

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        if not is_session_file(path):
            return None

        try:
            relative = path.relative_to(self.projects_dir)
        except ValueError:
            return None

        # Codex stores files under YYYY/MM/DD/, never directly under
        # SESSIONS_DIR. Anything shallower is unrecognized.
        if len(relative.parts) < 2:
            return None

        # Extract session_id + cwd (+ optional parent_session_id) from
        # the first JSONL line. This is blocking I/O (open + readline +
        # orjson parse), so we offload it to a worker thread to keep
        # the watcher event loop snappy.
        meta = await asyncio.to_thread(extract_session_meta, path)
        if meta is None:
            # Empty file, unreadable, or missing session_meta — silently
            # skip; sync_and_broadcast won't be called.
            return None

        project_id = path_to_project_id(meta.cwd)
        session_type = (
            SessionType.SUBAGENT if meta.parent_session_id else SessionType.SESSION
        )
        return ParsedSessionFile(
            project_id,
            meta.session_id,
            session_type,
            file_path=str(relative),
            parent_session_id=meta.parent_session_id,
        )

    async def _fetch_initial_title(self, parsed: ParsedSessionFile) -> str | None:
        # Top-level sessions only — subagents don't carry a user-facing name.
        if parsed.type != SessionType.SESSION:
            return None
        from .titles import read_title_from_codex
        return await read_title_from_codex(parsed.session_id)

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()

    async def _after_compaction_synced(self, session_id: str) -> None:
        # A COMPACT_SUMMARY line just landed in live sync. Tell the Codex
        # agent manager neutrally ("this session was compacted"). The manager
        # forwards it to a live agent if there is one; the agent itself
        # decides — via its own flag — whether it had a manual ``/compact``
        # in flight (and so should leave ASSISTANT_TURN) or whether this was
        # an auto-compaction it can ignore. No live manager (e.g. a
        # compute-only process) → nothing to notify.
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        await manager.notify_compacted(session_id)


# ---- Singleton accessor ----

_watcher: CodexSessionsWatcher | None = None


def get_watcher() -> CodexSessionsWatcher:
    """Return the process-local Codex watcher singleton.

    Callers (orchestrator) should go through this rather than
    instantiating their own watcher, so the stop/boost events and
    fast-poll deadline are shared.
    """
    global _watcher
    if _watcher is None:
        _watcher = CodexSessionsWatcher()
    return _watcher
