"""
Background task for monitoring Codex status via OpenAI's status page.

Periodically polls OpenAI's status page (incident.io-backed but exposing a
Statuspage v2-compatible JSON endpoint) to detect when the Codex API
component is experiencing issues, and broadcasts status changes to all
connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

import httpx
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Stop event for statuspage task
_statuspage_stop_event: asyncio.Event | None = None

# Cache the last known status (set by the check task, read on WS connect)
_last_known_status: str | None = None
_last_known_updated_at: str | None = None

# Interval for statuspage check: 2 minutes in seconds
STATUSPAGE_INTERVAL = 2 * 60

# Statuspage v2-compatible endpoint exposed by OpenAI's incident.io status page
COMPONENTS_URL = "https://status.openai.com/api/v2/components.json"

# The component we monitor — Codex CLI traffic goes through the Codex API
# component, which is the most direct signal for the Codex provider's runtime.
CODEX_COMPONENT_NAME = "Codex API"


class ComponentStatus(NamedTuple):
    """Status of the Codex API component from the status page."""

    status: str
    updated_at: str


def get_statuspage_stop_event() -> asyncio.Event:
    """Get or create the stop event for the statuspage task."""
    global _statuspage_stop_event
    if _statuspage_stop_event is None:
        _statuspage_stop_event = asyncio.Event()
    return _statuspage_stop_event


def stop_statuspage_task() -> None:
    """Signal the statuspage task to stop."""
    global _statuspage_stop_event
    if _statuspage_stop_event is not None:
        _statuspage_stop_event.set()


async def _fetch_codex_status() -> ComponentStatus | None:
    """Fetch the current status of the Codex API component.

    Queries the Statuspage-compatible components endpoint and extracts the
    Codex API component. Returns None if the component is not found or
    the request fails.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(COMPONENTS_URL)
        response.raise_for_status()
        data = response.json()

    for component in data.get("components", []):
        if component.get("name") == CODEX_COMPONENT_NAME:
            return ComponentStatus(
                status=component["status"],
                updated_at=component["updated_at"],
            )

    return None


def _build_statuspage_message(status: str) -> dict:
    """Build the codex:openai_status message payload."""
    return {
        "type": "codex:openai_status",
        "status": status,
    }


async def _broadcast_status_change(status: str) -> None:
    """Broadcast a codex:openai_status message to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": _build_statuspage_message(status),
        },
    )


def get_statuspage_message_for_connection() -> dict | None:
    """Build a codex:openai_status message if Codex is not operational.

    Returns the message dict, or None if status is operational or unknown.
    Called by the WebSocket consumer on client connect.
    """
    if _last_known_status is None or _last_known_status == "operational":
        return None
    return _build_statuspage_message(_last_known_status)


async def start_statuspage_task() -> None:
    """
    Background task that periodically checks Codex status on OpenAI's status page.

    Runs until stop event is set:
    - Checks status immediately on startup
    - Then waits STATUSPAGE_INTERVAL before the next check
    - Broadcasts codex:openai_status only when the status actually changes
    - Handles graceful shutdown via stop event
    """
    global _last_known_status, _last_known_updated_at

    stop_event = get_statuspage_stop_event()

    logger.info("Statuspage task started")

    while not stop_event.is_set():
        try:
            result = await _fetch_codex_status()
            if result:
                # Only act if updated_at changed (something happened on the status page)
                if result.updated_at != _last_known_updated_at:
                    old_status = _last_known_status
                    _last_known_updated_at = result.updated_at

                    # Only broadcast if the status itself changed
                    if result.status != old_status:
                        _last_known_status = result.status
                        # Don't broadcast on the very first check (startup)
                        # — the on-connect message handles informing new clients
                        if old_status is not None:
                            logger.info(
                                "Codex status changed: %s -> %s",
                                old_status,
                                result.status,
                            )
                            await _broadcast_status_change(result.status)
                        else:
                            logger.info("Codex initial status: %s", result.status)
            else:
                logger.warning("Statuspage check: Codex API component not found")
        except Exception as e:
            logger.warning("Statuspage check failed: %s", e)

        # Wait for the next check interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=STATUSPAGE_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to check again
            pass

    logger.info("Statuspage task stopped")
