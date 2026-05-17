"""Heartbeat file written by the live server.

The CLI reads ``<data_dir>/.server-heartbeat`` to fail-fast when no server
is running (or the server is still starting up before the heartbeat task
has launched). The file is empty; only its mtime matters.

Period: 5 seconds. The CLI's staleness threshold is 15 seconds (3x the
period) to absorb GC pauses and load spikes.
"""

from __future__ import annotations

import asyncio
import logging
import os

from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)

HEARTBEAT_PERIOD_SECONDS = 5
HEARTBEAT_FILENAME = ".server-heartbeat"


async def heartbeat_loop() -> None:
    """Touch ``<data_dir>/.server-heartbeat`` forever.

    Designed to be launched as an asyncio background task once the boot
    sequence (in particular ``migrate``) has completed.
    """
    path = get_data_dir() / HEARTBEAT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            path.touch(exist_ok=True)
            os.chmod(path, 0o600)  # idempotent
        except Exception:
            logger.exception("heartbeat: failed to update %s", path)
        await asyncio.sleep(HEARTBEAT_PERIOD_SECONDS)
