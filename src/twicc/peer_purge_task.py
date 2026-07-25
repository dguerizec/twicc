"""Periodic purge of peer-message attachment bytes (design §3.2, decision §10.4).

Deferred purge: attachment BYTES are dropped 7 days after a message resolves;
the text, ``attachments_meta`` and the row itself stay forever (history for the
lifetime of the relationship). The loop structure mirrors
``session_dirs_cleanup_task``; the writes follow the periodic-task convention —
one :class:`_PurgePeerAttachmentsJob` submitted to the DB writer via
:func:`submit_async_job` (precedents: ``_PersistProviderPricesJob``,
``_MarkSessionsIndexedJob``).

No broadcast on purge (v1): history views read the ``purged`` flag from REST
when they open; a per-row ``peer_message_updated`` broadcast would only matter
for a client staring at week-old history at the exact purge instant.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider

logger = logging.getLogger(__name__)

PEER_PURGE_INTERVAL = 6 * 60 * 60  # seconds
PEER_ATTACHMENT_RETENTION = timedelta(days=7)


class _PurgePeerAttachmentsJob(NamedTuple):
    """Async-queue job that runs :func:`_apply_purge_peer_attachments_job`.

    Cross-provider (``provider = None``): peer messages belong to no provider.
    ``future`` resolves to the number of messages purged.
    """

    future: asyncio.Future  # → int (messages purged)
    provider: Provider | None = None


def _apply_purge_peer_attachments_job(job: _PurgePeerAttachmentsJob) -> int:
    """Sync apply — runs on a worker thread through ``db_writer._settle_async_job``."""
    with transaction.atomic():
        return purge_expired_attachment_bytes()


def purge_expired_attachment_bytes(now: datetime | None = None) -> int:
    """Drop attachment bytes from messages resolved before the retention window.

    Keeps ``text`` and ``attachments_meta`` (names/sizes survive), stamps
    ``purged_at``. Returns the number of rows purged.
    """
    from twicc.core.models import PeerMessage

    now = now or datetime.now(tz=timezone.utc)
    cutoff = now - PEER_ATTACHMENT_RETENTION
    purged = 0
    candidates = PeerMessage.objects.filter(resolved_at__lt=cutoff, purged_at__isnull=True)
    for message in candidates:
        payload = message.payload or {}
        if not payload.get("images") and not payload.get("documents"):
            continue
        payload["images"] = []
        payload["documents"] = []
        message.payload = payload
        message.purged_at = now
        message.save(update_fields=["payload", "purged_at"])
        purged += 1
    if purged:
        logger.info("Peer purge: dropped attachment bytes from %d message(s)", purged)
    return purged


async def _run_purge_cycle() -> int:
    from twicc.providers.db_writer import submit_async_job

    loop = asyncio.get_running_loop()
    return await submit_async_job(_PurgePeerAttachmentsJob(future=loop.create_future()))


async def start_peer_purge_task(stop_event: asyncio.Event) -> None:
    """Periodic loop: every :data:`PEER_PURGE_INTERVAL`, purge expired bytes."""
    logger.info("Peer attachment purge task started")
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=PEER_PURGE_INTERVAL)
            except asyncio.TimeoutError:
                # Timeout means it's time to purge again.
                pass
            else:
                # stop_event fired — exit before another pass.
                break

            try:
                await _run_purge_cycle()
            except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.exception("Peer attachment purge cycle failed")
    finally:
        logger.info("Peer attachment purge task stopped")
