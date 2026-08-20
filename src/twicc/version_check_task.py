"""
Background task for checking PyPI for new TwiCC versions.

Periodically queries the PyPI JSON API to detect when a newer stable
version of TwiCC is available, and broadcasts an update_available
message to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from channels.layers import get_channel_layer
from django.conf import settings
from packaging.version import Version

logger = logging.getLogger(__name__)

# Stop event for version check task
_version_check_stop_event: asyncio.Event | None = None

# Cache the latest known version (set by the check task, read on WS connect)
_latest_known_version: Version | None = None

# Interval for version check: 15 minutes in seconds
VERSION_CHECK_INTERVAL = 15 * 60

# PyPI JSON API endpoint
PYPI_URL = "https://pypi.org/pypi/twicc/json"

# GitHub releases URL template
GITHUB_RELEASE_URL = "https://github.com/twidi/twicc/releases/tag/v{version}"


def get_version_check_stop_event() -> asyncio.Event:
    """Get or create the stop event for the version check task."""
    global _version_check_stop_event
    if _version_check_stop_event is None:
        _version_check_stop_event = asyncio.Event()
    return _version_check_stop_event


def stop_version_check_task() -> None:
    """Signal the version check task to stop."""
    global _version_check_stop_event
    if _version_check_stop_event is not None:
        _version_check_stop_event.set()


def _get_latest_stable_version(releases: dict) -> Version | None:
    """Extract the latest stable version from PyPI releases dict.

    Filters out pre-releases (alpha, beta, rc, dev) and returns
    the highest stable version, or None if no stable versions exist.
    """
    stable_versions = []
    for version_str in releases:
        try:
            v = Version(version_str)
            if not v.is_prerelease and not v.is_devrelease:
                stable_versions.append(v)
        except Exception:
            # Skip unparseable version strings
            continue
    return max(stable_versions) if stable_versions else None


async def _check_pypi_version() -> Version | None:
    """Query PyPI for the latest stable version of twicc.

    Returns the latest stable Version, or None if the check fails.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(PYPI_URL)
        response.raise_for_status()
        data = response.json()
        return _get_latest_stable_version(data.get("releases", {}))


def _build_update_available_message(latest_version: Version) -> dict:
    """Build the update_available message payload."""
    return {
        "type": "update_available",
        "current_version": settings.APP_VERSION,
        "latest_version": str(latest_version),
        "release_url": GITHUB_RELEASE_URL.format(version=latest_version),
    }


async def _broadcast_update_available(latest_version: Version) -> None:
    """Broadcast update_available message to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": _build_update_available_message(latest_version),
        },
    )


def get_update_available_message() -> dict | None:
    """Build an update_available message if a newer version is known.

    Returns the message dict, or None if no update is available.
    Called by the WebSocket consumer on client connect.
    """
    if _latest_known_version is None:
        return None
    current = Version(settings.APP_VERSION)
    if _latest_known_version <= current:
        return None
    return _build_update_available_message(_latest_known_version)


async def start_version_check_task() -> None:
    """
    Background task that periodically checks PyPI for new TwiCC versions.

    Runs until stop event is set:
    - Checks PyPI immediately on startup
    - Then waits VERSION_CHECK_INTERVAL before the next check
    - Broadcasts update_available if a newer stable version is found
    - Handles graceful shutdown via stop event
    """
    global _latest_known_version

    stop_event = get_version_check_stop_event()
    current_version = Version(settings.APP_VERSION)

    logger.info("Version check task started (current: %s)", current_version)

    while not stop_event.is_set():
        try:
            latest = await _check_pypi_version()
            if latest and latest > current_version:
                logger.info("New version available: %s (current: %s)", latest, current_version)
                _latest_known_version = latest
                await _broadcast_update_available(latest)
            else:
                logger.debug("Version check: up to date (current: %s, latest: %s)", current_version, latest)
        except Exception as e:
            logger.warning("Version check failed: %s", e)

        # Wait for the next check interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=VERSION_CHECK_INTERVAL)
        except TimeoutError:
            # Timeout means it's time to check again
            pass

    logger.info("Version check task stopped")
