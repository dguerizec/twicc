"""
Codex title management.

Thin async wrapper around the Codex SDK's ``thread/name/set`` RPC for
renaming Codex threads. Unlike Claude Code, Codex stores thread
metadata in its own state DB (single source of truth), so there is no
anti-stale-write protection (no ``protect_title`` machinery) — the
matching JSONL rollout file never carries the title in the first place.
"""

from __future__ import annotations

import logging

from codex_app_server import AppServerConfig, AsyncCodex

from .bin import resolve_bundled_binary

logger = logging.getLogger(__name__)


async def rename_thread_via_sdk(thread_id: str, title: str) -> None:
    """Set the Codex thread's display name via ``thread/name/set``.

    Spawns a short-lived ``AsyncCodex`` transport (one initialize + one
    ``thread/resume`` + one ``thread/name/set`` RPC). Cannot piggy-back
    on an existing ``AsyncCodex`` because the SDK only allows one
    streamed turn consumer at a time across the whole client (see the
    ``_active_turn_consumer`` guard in ``codex_app_server.client``):
    sharing it with an active agent would either starve the agent or
    fail-fast on a name update.

    Raises on failure so the caller can decide how to react. The HTTP
    rename endpoint currently swallows the error (the DB title is
    already updated; the watcher / next session reload will reconcile).
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    try:
        async with AsyncCodex(config=config) as codex:
            thread = await codex.thread_resume(thread_id)
            await thread.set_name(title)
    except Exception as e:
        logger.warning("Codex thread/name/set failed for %s: %s", thread_id, e)
        raise


async def read_title_from_codex(thread_id: str) -> str | None:
    """Read the Codex thread's current display name from the state DB.

    Returns ``None`` if the thread has no name set, or on any error
    (logged at WARNING). Used by the watcher on first session
    materialisation to import a title that was set via the Codex CLI.
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    try:
        async with AsyncCodex(config=config) as codex:
            # ``AsyncCodex`` exposes no top-level ``thread_read``; like
            # ``rename_thread_via_sdk`` we go through ``thread_resume``
            # to get an ``AsyncThread`` handle and call ``.read()`` on
            # it (defined at ``codex_app_server/api.py:649-653``).
            thread = await codex.thread_resume(thread_id)
            response = await thread.read(include_turns=False)
            name = response.thread.name
            return name if name else None
    except Exception as e:
        logger.warning("Codex thread/read failed for %s: %s", thread_id, e)
        return None


async def bulk_sync_titles_from_codex() -> dict[str, str]:
    """Read every Codex thread's display name via state-DB-only list.

    Returns a ``{thread_id: name}`` mapping (only entries with a non-empty
    name are included). Returns an empty dict on error (logged at WARNING).
    Pagination is exhaustive — there is no upper bound on the number of
    threads, but the call is cheap because ``use_state_db_only=True``
    skips JSONL rollout scanning.
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    titles: dict[str, str] = {}
    try:
        async with AsyncCodex(config=config) as codex:
            cursor: str | None = None
            while True:
                page = await codex.thread_list(
                    use_state_db_only=True,
                    cursor=cursor,
                    limit=100,
                )
                for thread in page.data:
                    if thread.name:
                        titles[thread.id] = thread.name
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
    except Exception as e:
        logger.warning("Codex bulk thread/list failed: %s", e)
    return titles
