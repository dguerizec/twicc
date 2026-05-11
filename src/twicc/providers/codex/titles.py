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
