"""Watcher for hybrid CLI hook event files.

Hook commands injected at hybrid launch (see ``hybrid/launch.py``) drop
``<session_id>__<event>__<nonce>.json`` files into ``get_hybrid_hooks_dir()``;
this watcher feeds them to the Claude Code agent manager and deletes them —
EXCEPT ``PermissionRequest`` drops the agent takes ownership of
(``HybridHookOutcome.OWNED``): those stay on disk until the pending request
resolves, so the boot scan can re-feed them after a TwiCC restart while the
hook process (a child of the CLI, not of TwiCC) is still polling for its
``.status.json`` answer. File-based on purpose: no HTTP endpoint to exempt
from the password middleware, no token to leak through ``ps`` — write access
to the data dir IS the authentication.

Only ``PermissionRequest`` is injected in V1, but any event name is parsed
and routed (forward-compat with the V2 ideas in the design doc §7); events
the manager does not handle are logged and dropped.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import orjson
from watchfiles import Change, awatch

from twicc.paths import get_hybrid_hooks_dir
from twicc.providers.claude_code.agent.hybrid.signals import HybridHookOutcome

logger = logging.getLogger(__name__)

EVENT_SUFFIX = ".json"
TMP_SUFFIX = ".tmp"
# Answers written by the hybrid agent next to the drop files (drop-requests
# convention); consumed by the polling hook, NEVER by this watcher. Without
# this exclusion a status file would parse as a valid 3-part event name and
# get routed then deleted.
STATUS_SUFFIX = ".status.json"


class HybridHooksWatcher:
    def __init__(self) -> None:
        self.directory = get_hybrid_hooks_dir()
        self._in_flight: set[str] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        # Start awatch FIRST so nothing dropped during the boot scan is
        # missed (same pattern as DropRequestsWatcher). The boot scan
        # handles events that fired while TwiCC was down: boot adoption of
        # surviving hybrid sessions runs before this watcher starts, so a
        # leftover PermissionRequest of a still-pending prompt reaches the
        # adopted agent, while events for long-gone sessions fall through
        # harmlessly (handled=False → deleted).
        watch_task = asyncio.ensure_future(self._watch_loop())
        await self._scan_existing()
        try:
            await watch_task
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Watch loop / boot scan
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            # Filename order within a burst: the nanosecond nonce makes it
            # chronological enough across a single session's events.
            paths = sorted(
                Path(raw) for change_type, raw in changes
                if change_type == Change.added
            )
            for path in paths:
                if not self._is_event_file(path):
                    continue
                if path.name in self._in_flight:
                    continue
                asyncio.ensure_future(self._process_file(path))

    async def _scan_existing(self) -> None:
        # Orphan status sweep first: an answer file whose drop is gone can
        # never be consumed (its hook died — TUI Esc kills it, or the CLI
        # reaped it at timeout). Mirrors the drop-requests watcher's boot
        # cleanup.
        for path in sorted(self.directory.glob(f"*{STATUS_SUFFIX}")):
            drop = path.with_name(path.name.removesuffix(STATUS_SUFFIX) + EVENT_SUFFIX)
            if not drop.exists():
                logger.info("[HybridHooksWatcher] boot cleanup orphan status %s", path.name)
                await asyncio.to_thread(path.unlink, missing_ok=True)

        for path in sorted(self.directory.glob(f"*{EVENT_SUFFIX}")):
            if not self._is_event_file(path):
                continue
            if path.name in self._in_flight:
                continue
            logger.info("[HybridHooksWatcher] boot processes %s", path.name)
            asyncio.ensure_future(self._process_file(path))

    @staticmethod
    def _is_event_file(path: Path) -> bool:
        return (
            path.name.endswith(EVENT_SUFFIX)
            and not path.name.endswith(TMP_SUFFIX)
            and not path.name.endswith(STATUS_SUFFIX)
        )

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------

    async def _process_file(self, path: Path) -> None:
        self._in_flight.add(path.name)
        try:
            parts = path.stem.split("__")
            if len(parts) != 3 or not parts[0] or not parts[1]:
                logger.warning(
                    "[HybridHooksWatcher] malformed event filename, dropping: %s",
                    path.name,
                )
                await asyncio.to_thread(path.unlink, missing_ok=True)
                return
            session_id, event, nonce = parts

            payload: dict = {}
            try:
                content = await asyncio.to_thread(path.read_bytes)
                if content:
                    parsed = orjson.loads(content)
                    if isinstance(parsed, dict):
                        payload = parsed
            except Exception:
                logger.warning(
                    "[HybridHooksWatcher] unparseable payload for %s (event kept "
                    "with empty payload)", path.name,
                )

            from twicc.providers.claude_code.agent.manager import (
                get_claude_code_agent_manager,
            )

            outcome = await get_claude_code_agent_manager().handle_hybrid_hook(
                session_id, event, payload, nonce,
            )
            if outcome is HybridHookOutcome.UNHANDLED:
                # Stale event (no live hybrid agent) or an event name the
                # manager does not route — dropped, never retried.
                logger.info(
                    "[HybridHooksWatcher] unhandled %s event for session %s "
                    "(dropped)", event, session_id,
                )
            if outcome is not HybridHookOutcome.OWNED:
                await asyncio.to_thread(path.unlink, missing_ok=True)
        except Exception:
            logger.exception("[HybridHooksWatcher] processing failed for %s", path.name)
            await asyncio.to_thread(path.unlink, missing_ok=True)
        finally:
            self._in_flight.discard(path.name)


_watcher_instance: HybridHooksWatcher | None = None


def get_hybrid_hooks_watcher() -> HybridHooksWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = HybridHooksWatcher()
    return _watcher_instance
