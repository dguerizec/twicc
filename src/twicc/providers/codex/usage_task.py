"""Codex usage sync orchestration.

Owns the lifecycle of the Codex usage sync loop (start/stop event,
interval, error handling). Delegates the cross-provider building blocks
(broadcasting, latest-snapshot lookup) to :mod:`twicc.usage_task`.

Mirrors :mod:`twicc.providers.claude_code.usage_task` — same structure,
different fetcher.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from twicc.core.enums import Provider
from twicc.usage_task import broadcast_usage_updated

from .credentials import CREDENTIALS_PATH
from .helpers import CodexHelpers
from .usage import fetch_and_save_usage

logger = logging.getLogger(__name__)

_usage_sync_stop_event: asyncio.Event | None = None


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the Codex usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_usage_sync_task() -> None:
    """Signal the Codex usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


async def start_usage_sync_task() -> None:
    """Periodically fetch and store Codex usage quotas.

    Runs until :func:`stop_usage_sync_task` is called:
    - Executes :func:`fetch_and_save_usage` immediately on startup
    - Then waits :attr:`CodexHelpers.USAGE_SYNC_INTERVAL` before the next fetch
    - Handles graceful shutdown via the stop event

    The fetch operation runs in a thread to avoid blocking the event
    loop, as it involves an HTTP request to ChatGPT's ``wham/usage``
    endpoint.
    """
    interval = CodexHelpers.USAGE_SYNC_INTERVAL
    stop_event = get_usage_sync_stop_event()
    # Reset for hot-restart support — see auth_task.start_auth_task().
    stop_event.clear()

    logger.info("Codex usage sync task started")

    while not stop_event.is_set():
        success = False
        try:
            # On macOS, skip the OAuth token refresh only when Codex is in
            # keyring storage mode — there, refreshing makes the bundled codex
            # binary rewrite the "Codex Auth" Keychain item, resetting its ACL
            # and popping an unprompted authorization dialog (same issue as
            # Claude Code). The CLI deletes ~/.codex/auth.json whenever it
            # switches to keyring, so the file's presence is a reliable, always-
            # current signal of where the binary writes: file present → refresh
            # rewrites the file (no prompt) → allow; file absent (keyring) →
            # refresh rewrites the Keychain → skip. Re-evaluated every cycle
            # because the mode can change at runtime (unlike Claude Code, which
            # is always Keychain on macOS). The token is then refreshed by real
            # sessions or on demand via the sidebar "Refresh now" button.
            allow_refresh = sys.platform != "darwin" or CREDENTIALS_PATH.is_file()
            snapshot = await fetch_and_save_usage(allow_refresh=allow_refresh)
            if snapshot:
                success = True
                logger.info(
                    "Codex usage sync completed: 5h=%.1f%% (time: %.1f%%), 7d=%.1f%% (time: %.1f%%)",
                    snapshot.five_hour_utilization or 0,
                    snapshot.five_hour_temporal_pct or 0,
                    snapshot.seven_day_utilization or 0,
                    snapshot.seven_day_temporal_pct or 0,
                )
            else:
                logger.warning("Codex usage sync: no data (credentials missing or API error)")
        except Exception as e:
            logger.error("Codex usage sync failed: %s", e, exc_info=True)

        try:
            await broadcast_usage_updated(Provider.CODEX, success)
        except Exception as e:
            logger.error("Codex usage broadcast failed: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Codex usage sync task stopped")
