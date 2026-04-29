"""
Background task for monitoring Claude Code status via Anthropic's status page.

Periodically polls the Atlassian Statuspage API to detect when the
Claude Code component is experiencing issues, and broadcasts status
changes to all connected WebSocket clients.
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

# Atlassian Statuspage API endpoint for Claude
COMPONENTS_URL = "https://status.claude.com/api/v2/components.json"

# The component we monitor
CLAUDE_CODE_COMPONENT_NAME = "Claude Code"


class ComponentStatus(NamedTuple):
    """Status of the Claude Code component from the status page."""

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


async def _fetch_claude_code_status() -> ComponentStatus | None:
    """Fetch the current status of the Claude Code component.

    Queries the Atlassian Statuspage components API and extracts the
    Claude Code component. Returns None if the component is not found
    or the request fails.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(COMPONENTS_URL)
        response.raise_for_status()
        data = response.json()

    for component in data.get("components", []):
        if component.get("name") == CLAUDE_CODE_COMPONENT_NAME:
            return ComponentStatus(
                status=component["status"],
                updated_at=component["updated_at"],
            )

    return None


def _build_statuspage_message(status: str) -> dict:
    """Build the claude_status message payload."""
    return {
        "type": "claude_status",
        "status": status,
    }


async def _broadcast_status_change(status: str) -> None:
    """Broadcast a claude_status message to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": _build_statuspage_message(status),
        },
    )


def get_statuspage_message_for_connection() -> dict | None:
    """Build a claude_status message if Claude Code is not operational.

    Returns the message dict, or None if status is operational or unknown.
    Called by the WebSocket consumer on client connect.
    """
    if _last_known_status is None or _last_known_status == "operational":
        return None
    return _build_statuspage_message(_last_known_status)


async def start_statuspage_task() -> None:
    """
    Background task that periodically checks Claude Code status on Anthropic's status page.

    Runs until stop event is set:
    - Checks status immediately on startup
    - Then waits STATUSPAGE_INTERVAL before the next check
    - Broadcasts claude_status only when the status actually changes
    - Handles graceful shutdown via stop event
    """
    global _last_known_status, _last_known_updated_at

    stop_event = get_statuspage_stop_event()

    logger.info("Statuspage task started")

    while not stop_event.is_set():
        try:
            result = await _fetch_claude_code_status()
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
                                "Claude Code status changed: %s -> %s",
                                old_status,
                                result.status,
                            )
                            await _broadcast_status_change(result.status)
                        else:
                            logger.info("Claude Code initial status: %s", result.status)
            else:
                logger.warning("Statuspage check: Claude Code component not found")
        except Exception as e:
            logger.warning("Statuspage check failed: %s", e)

        # Wait for the next check interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=STATUSPAGE_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to check again
            pass

    logger.info("Statuspage task stopped")
