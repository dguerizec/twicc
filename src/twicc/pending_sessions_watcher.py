"""Watcher for CLI-dropped session-creation requests.

Watches ``<data_dir>/sessions-pending/`` for new ``<request_uuid>.json``
files dropped by ``twicc create-session``. Calls
:func:`create_session_from_payload` and writes a ``<request_uuid>.status.json``
file the CLI polls. Cleanup is the CLI's responsibility in the nominal
case; this watcher only handles dead-letter cleanup at boot (see
spec §5.5).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from datetime import datetime, timezone

import orjson
from watchfiles import Change, awatch

from twicc.core.services.session_creation import create_session_from_payload
from twicc.paths import get_data_dir


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

logger = logging.getLogger(__name__)

DIRECTORY_NAME = "sessions-pending"
DROP_SUFFIX = ".json"
STATUS_SUFFIX = ".status.json"
TMP_SUFFIX = ".tmp"


class PendingSessionsWatcher:
    def __init__(self) -> None:
        self.directory = get_data_dir() / DIRECTORY_NAME
        self._in_flight: set[str] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Start awatch FIRST to avoid missing files dropped during the boot scan
        watch_task = asyncio.ensure_future(self._watch_loop())
        await self._scan_existing()
        await self._cleanup_orphan_status_files()
        try:
            await watch_task
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------
    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            for change_type, raw_path in changes:
                p = Path(raw_path)
                if change_type != Change.added:
                    continue
                if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                    continue
                if p.suffix != DROP_SUFFIX:
                    continue
                if p.stem in self._in_flight:
                    continue
                asyncio.ensure_future(self._process_file(p))

    # ------------------------------------------------------------------
    # Boot scan
    # ------------------------------------------------------------------
    async def _scan_existing(self) -> None:
        for p in sorted(self.directory.glob(f"*{DROP_SUFFIX}")):
            if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                continue
            if p.stem in self._in_flight:
                continue
            status_path = self.directory / f"{p.stem}{STATUS_SUFFIX}"
            if status_path.exists():
                # CLI crashed before deleting both files. Session already created
                # / rejected / failed — just clean up.
                logger.info("[PendingSessionsWatcher] boot cleanup drop+status %s", p.stem)
                p.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
            else:
                # Drop file orphaned by a server restart — process normally, no
                # timing check (cf. spec §5.5).
                logger.info("[PendingSessionsWatcher] boot processes drop %s", p.stem)
                asyncio.ensure_future(self._process_file(p))

    async def _cleanup_orphan_status_files(self) -> None:
        for p in sorted(self.directory.glob(f"*{STATUS_SUFFIX}")):
            request_uuid = p.name[:-len(STATUS_SUFFIX)]
            drop_path = self.directory / f"{request_uuid}{DROP_SUFFIX}"
            if not drop_path.exists():
                logger.info("[PendingSessionsWatcher] boot cleanup orphan status %s", request_uuid)
                p.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------
    async def _process_file(self, path: Path) -> None:
        request_uuid = path.stem
        self._in_flight.add(request_uuid)
        try:
            try:
                content = await asyncio.to_thread(path.read_bytes)
                data = await asyncio.to_thread(orjson.loads, content)
            except Exception as e:
                logger.exception("[PendingSessionsWatcher] parse failed for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"Could not parse drop-file: {e}",
                })
                return  # CLI will delete both drop + status files

            await self._write_status(request_uuid, {"status": "received"})
            logger.info("[PendingSessionsWatcher] received %s", request_uuid)

            payload = data.get("payload") or {}
            kind = payload.get("kind", "create")  # BC: pre-kind payloads = create

            if kind == "create":
                service = create_session_from_payload
                success_status = "created"
            elif kind == "send":
                from twicc.core.services.send_message import (
                    send_message_to_session_from_payload,
                )
                service = send_message_to_session_from_payload
                success_status = "sent"
            elif kind == "update_settings":
                from twicc.core.services.session_update import (
                    update_session_settings_from_payload,
                )
                service = update_session_settings_from_payload
                success_status = "updated"
            else:
                logger.warning("[PendingSessionsWatcher] unknown kind for %s: %r",
                               request_uuid, kind)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"Unknown payload kind: {kind!r}",
                })
                return  # CLI will delete both drop + status files

            try:
                result = await service(payload)
            except Exception as e:
                logger.exception("[PendingSessionsWatcher] service raised for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"{type(e).__name__}: {e}",
                })
                return  # CLI will delete both drop + status files

            if result.success:
                logger.info("[PendingSessionsWatcher] %s %s -> %s",
                            success_status, request_uuid, result.session_id)
                await self._write_status(request_uuid, {
                    "status": success_status,
                    "session_id": result.session_id,
                    "provider": result.provider,
                    "project_id": result.project_id,
                })
            else:
                logger.warning("[PendingSessionsWatcher] rejected %s: %s",
                               request_uuid, result.errors)
                await self._write_status(request_uuid, {
                    "status": "rejected",
                    "errors": [e._asdict() for e in (result.errors or [])],
                })
            # No unlink here: the CLI cleans up its own drop-file once it
            # observes the final status. The watcher only deletes during the
            # boot scan, for files left behind by a crashed CLI
            # (cf. _scan_existing).
        finally:
            self._in_flight.discard(request_uuid)

    # ------------------------------------------------------------------
    # Status file writer (atomic via tmp + rename)
    # ------------------------------------------------------------------
    async def _write_status(self, request_uuid: str, data: dict) -> None:
        # Merge timestamps. The CLI relies on them for the wording.
        data.setdefault("request_uuid", request_uuid)
        if data["status"] == "received":
            data.setdefault("received_at", _iso_now())
        elif data["status"] == "created":
            data.setdefault("created_at", _iso_now())
        elif data["status"] == "sent":
            data.setdefault("sent_at", _iso_now())
        elif data["status"] == "updated":
            data.setdefault("updated_at", _iso_now())
        elif data["status"] == "rejected":
            data.setdefault("rejected_at", _iso_now())
        elif data["status"] == "failed":
            data.setdefault("failed_at", _iso_now())

        path = self.directory / f"{request_uuid}{STATUS_SUFFIX}"
        tmp = path.with_suffix(path.suffix + TMP_SUFFIX)
        await asyncio.to_thread(tmp.write_bytes, orjson.dumps(data))
        await asyncio.to_thread(os.replace, tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


_watcher_instance: PendingSessionsWatcher | None = None


def get_pending_sessions_watcher() -> PendingSessionsWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = PendingSessionsWatcher()
    return _watcher_instance
