"""
Background task for monitoring Claude Code CLI authentication state.

Runs ``claude auth status --json`` on startup, then keeps polling every
30 seconds **only while the state is False**. Once the state flips to
True, the task goes idle and waits to be woken up by an external signal
(SDK ``authentication_failed``, manual recheck, etc.).
"""

from __future__ import annotations

import asyncio
import logging

from .auth import check_and_broadcast, get_auth_wake_event

logger = logging.getLogger(__name__)

# Stop event for the auth check task
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
    wake = get_auth_wake_event()
    wake.set()


async def _wait_either(events: list[asyncio.Event], *, timeout: float | None) -> None:
    """
    Wait until any of the given events is set, or the timeout elapses.

    Cancels its waiter tasks before returning. Does not raise on timeout.
    """
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
    """
    Background task that monitors Claude Code CLI authentication state.

    Behavior:
    - Runs a check on startup.
    - While the result is False (or any error), keeps polling every
      AUTH_CHECK_INTERVAL seconds.
    - As soon as the result becomes True, switches to idle mode and waits
      until the wake event is set (e.g. after ``mark_unauthenticated_and_broadcast``)
      or the stop event is set, then re-runs a check.
    """
    stop_event = get_auth_stop_event()
    # Reset for hot-restart support: a previous shutdown leaves stop_event
    # set, which would make the new task exit on its first ``is_set()``.
    stop_event.clear()
    wake_event = get_auth_wake_event()

    logger.info("Auth check task started")

    previous: bool | None = None

    while not stop_event.is_set():
        try:
            authenticated = await check_and_broadcast()
        except Exception as e:
            logger.warning("Auth check failed: %s", e)
            authenticated = False

        if authenticated != previous:
            logger.info("Claude Code CLI authenticated: %s", authenticated)
            previous = authenticated

        if stop_event.is_set():
            break

        if authenticated:
            # Idle: wait for an external wake-up (e.g. SDK auth_failed) or shutdown.
            wake_event.clear()
            await _wait_either([wake_event, stop_event], timeout=None)
        else:
            # Poll: wait AUTH_CHECK_INTERVAL or until shutdown / wake.
            wake_event.clear()
            await _wait_either([wake_event, stop_event], timeout=AUTH_CHECK_INTERVAL)

    logger.info("Auth check task stopped")
