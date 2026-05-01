"""Claude Code usage sync orchestration.

Owns the lifecycle of the Claude Code usage sync loop (start/stop event,
interval, error handling). Delegates the building blocks (fetch latest,
build message, broadcast) to :mod:`twicc.usage_task`.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.core.enums import Provider
from twicc.usage_task import broadcast_usage_updated
from .helpers import ClaudeCodeHelpers
from .usage import fetch_and_save_usage

logger = logging.getLogger(__name__)

# Stop event for usage sync task
_usage_sync_stop_event: asyncio.Event | None = None


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the Claude Code usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_usage_sync_task() -> None:
    """Signal the Claude Code usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


async def start_usage_sync_task() -> None:
    """Periodically fetch and store Claude Code usage quotas.

    Runs until :func:`stop_usage_sync_task` is called:
    - Executes :func:`fetch_and_save_usage` immediately on startup
    - Then waits :attr:`ClaudeCodeHelpers.USAGE_SYNC_INTERVAL` before the next fetch
    - Handles graceful shutdown via the stop event

    The fetch operation runs in a thread to avoid blocking the event
    loop, as it involves an HTTP request to the Anthropic API.
    """
    interval = ClaudeCodeHelpers.USAGE_SYNC_INTERVAL
    stop_event = get_usage_sync_stop_event()

    logger.info("Usage sync task started")

    while not stop_event.is_set():
        success = False
        try:
            snapshot = await asyncio.to_thread(fetch_and_save_usage)
            if snapshot:
                success = True
                logger.info(
                    "Usage sync completed: 5h=%.1f%% (time: %.1f%%), 7d=%.1f%% (time: %.1f%%)",
                    snapshot.five_hour_utilization or 0,
                    snapshot.five_hour_temporal_pct or 0,
                    snapshot.seven_day_utilization or 0,
                    snapshot.seven_day_temporal_pct or 0,
                )
            else:
                logger.warning("Usage sync: no data (credentials missing or API error)")
        except Exception as e:
            logger.error("Usage sync failed: %s", e, exc_info=True)

        # Broadcast to frontend (always sends latest snapshot from DB + success flag)
        try:
            await broadcast_usage_updated(Provider.CLAUDE_CODE, success)
        except Exception as e:
            logger.error("Usage broadcast failed: %s", e, exc_info=True)

        # Wait for the next sync interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            # Timeout means it's time to sync again
            pass

    logger.info("Usage sync task stopped")
