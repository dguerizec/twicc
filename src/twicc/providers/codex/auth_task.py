"""
Background task for monitoring Codex CLI authentication state.

Runs ``codex login status`` on startup, then keeps polling every 30 seconds
**only while the state is False**. Once the state flips to True, the task
goes idle and waits for an external wake-up (manual recheck) or shutdown.

Mirrors :mod:`twicc.providers.claude_code.auth_task` — same structure,
different binary.
"""

from __future__ import annotations

import asyncio
import logging

from .auth import check_and_broadcast, get_auth_wake_event

logger = logging.getLogger(__name__)

_auth_stop_event: asyncio.Event | None = None

# Interval between polls while the state is unauthenticated.
AUTH_CHECK_INTERVAL = 30


def get_auth_stop_event() -> asyncio.Event:
    """Get or create the stop event for the auth check task."""
    global _auth_stop_event
    if _auth_stop_event is None:
        _auth_stop_event = asyncio.Event()
    return _auth_stop_event


def stop_auth_task() -> None:
    """Signal the auth check task to stop."""
    global _auth_stop_event
    if _auth_stop_event is not None:
        _auth_stop_event.set()
    # Wake the loop if it's currently idle so it observes the stop signal.
    get_auth_wake_event().set()


async def _wait_either(events: list[asyncio.Event], *, timeout: float | None) -> None:
    """Wait until any of the given events is set, or the timeout elapses."""
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
    finally:
        for w in waiters:
            w.cancel()
        for w in waiters:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass


async def start_auth_task() -> None:
    """Background task that monitors Codex CLI authentication state."""
    stop_event = get_auth_stop_event()
    wake_event = get_auth_wake_event()

    logger.info("Codex auth check task started")

    previous: bool | None = None

    while not stop_event.is_set():
        try:
            authenticated = await check_and_broadcast()
        except Exception as e:
            logger.warning("Codex auth check failed: %s", e)
            authenticated = False

        if authenticated != previous:
            logger.info("Codex CLI authenticated: %s", authenticated)
            previous = authenticated

        if stop_event.is_set():
            break

        if authenticated:
            wake_event.clear()
            await _wait_either([wake_event, stop_event], timeout=None)
        else:
            wake_event.clear()
            await _wait_either([wake_event, stop_event], timeout=AUTH_CHECK_INTERVAL)

    logger.info("Codex auth check task stopped")
