"""
Startup progress tracking and broadcasting.

Maintains module-level state for each (provider, phase) tuple and broadcasts
progress updates via Django Channels to all connected WebSocket clients.

Provider-aware phases (initial_sync, background_compute) carry the provider
key emitting them so the frontend can aggregate totals across providers.
Provider-agnostic phases (e.g. search_index, which indexes sessions
regardless of the provider that produced them) pass ``provider=None``.

The state is also readable by UpdatesConsumer.connect() so clients joining
mid-startup receive the current progress immediately.
"""

from __future__ import annotations

import logging

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Module-level state: current startup progress for each (provider, phase) pair.
# Entries are created on first ``set_startup_progress`` call and kept after
# completion so clients connecting mid-startup can reconstruct the full picture.
_current_progress: dict[tuple[str | None, str], dict] = {}


def get_startup_progress() -> list[dict]:
    """Return the list of startup progress states for WS connection init.

    Called by UpdatesConsumer.connect() to send current progress to newly
    connected clients. Includes completed phases so reconnecting clients
    can show them as finished.
    """
    return list(_current_progress.values())


def set_startup_progress(
    phase: str,
    current: int,
    total: int,
    *,
    provider: str | None = None,
    completed: bool = False,
) -> None:
    """Update the module-level progress state for a (provider, phase) pair."""
    _current_progress[(provider, phase)] = {
        "type": "startup_progress",
        "provider": provider,
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
    }


async def broadcast_startup_progress(
    phase: str,
    current: int,
    total: int,
    *,
    provider: str | None = None,
    completed: bool = False,
) -> None:
    """Update state and broadcast progress via Django Channels.

    Updates the module-level state first (so new connections get the latest),
    then broadcasts to all connected WebSocket clients via the "updates" group.
    """
    set_startup_progress(phase, current, total, provider=provider, completed=completed)

    message = {
        "type": "startup_progress",
        "provider": provider,
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
    }

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )
