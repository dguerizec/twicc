"""Channel layer that never loses updates in silence.

``InMemoryChannelLayer`` discards messages in three ways, none of which the
client can detect and none of which raise or log:

1. **Queue full** — past ``capacity``, ``group_send`` swallows ``ChannelFull``
   and the frame is gone.
2. **Message expired** — past ``expiry``, ``_clean_expired`` drops the message
   *and* calls ``_remove_from_groups``, unsubscribing the channel from every
   group it belongs to.
3. **Group expired** — ``group_expiry`` seconds after ``group_add`` the channel
   is removed from the group whatever it does. The join timestamp is written
   once and never refreshed, so a perfectly healthy browser tab goes deaf
   24 hours (the library default) after connecting.

Modes 2 and 3 leave the socket open with the client subscribed to nothing: it
simply stops receiving, forever, with no way to notice. Mode 1 leaves a
half-applied stream, which is worse than a gap — a later frame can make no
sense without the one that was dropped.

This layer replaces silence with a signal. The first time a channel loses
anything, for any of these reasons, it is **broken**:

- its queue is flushed (everything in it belongs to a sequence already torn),
- a single ``resync_required`` frame is queued in its place,
- every later message for it is discarded.

The client receives that one frame and reloads, which opens a fresh connection
under a new channel name — a new, empty queue. Flushing is what makes the
delivery reliable: the queue that was full necessarily has room afterwards, so
the signal never needs to wait for the client to catch up.

Mode 3 is not handled here — it is switched off in ``settings.CHANNEL_LAYERS``
by setting ``group_expiry`` far beyond any real session. It exists to collect
consumers that vanish without notice, which cannot happen in-process: TwiCC's
consumers call ``group_discard`` on disconnect.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from channels.exceptions import ChannelFull
from channels.layers import InMemoryChannelLayer

logger = logging.getLogger(__name__)


# Seconds between two log lines about the same broken channel. The first loss
# is logged at once; further discards are counted and summarised on this
# cadence, so a channel that keeps receiving traffic after it broke cannot
# flood the log.
DEFAULT_DROP_LOG_WINDOW = 30.0


class TwiccChannelLayer(InMemoryChannelLayer):
    """In-memory layer that tells a client when it has missed updates."""

    #: ``data.type`` of the synthetic frame queued for a broken channel.
    RESYNC_MESSAGE_TYPE = "resync_required"

    def __init__(self, *args: Any, drop_log_window: float = DEFAULT_DROP_LOG_WINDOW, **kwargs: Any) -> None:
        # Consumed here so it never reaches BaseChannelLayer.__init__, which
        # would reject it: CHANNEL_LAYERS["CONFIG"] is passed to the backend as
        # keyword arguments, so every key must be a parameter of some __init__.
        self.drop_log_window = drop_log_window
        super().__init__(*args, **kwargs)

        # Channels whose stream is torn. They keep their group membership (so
        # the consumer's own bookkeeping stays coherent) but receive nothing
        # beyond the resync frame already queued for them.
        self._broken: set[str] = set()
        # Frames discarded for a broken channel since its last log line.
        self._discarded: dict[str, int] = {}
        self._last_log_at: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Loss detection
    # ------------------------------------------------------------------

    async def send(self, channel: str, message: dict) -> None:
        """Queue a message, or record the loss when it cannot be queued."""
        if channel in self._broken:
            self._count_discard(channel)
            return

        try:
            await super().send(channel, message)
        except ChannelFull:
            self._break_channel(channel, "its queue is full", message)
            # Re-raised so the library contract holds. ``group_send`` catches
            # and ignores it, which is exactly what we want now that the loss
            # has been turned into a signal.
            raise

    def _remove_from_groups(self, channel: str) -> None:
        """Break the channel instead of unsubscribing it.

        The base class calls this from ``_clean_expired`` for every message
        that outlives ``expiry``, dropping the channel from every group it
        belongs to. Nothing ever adds it back — ``group_add`` only runs in the
        consumer's ``connect`` — so the client goes permanently deaf with its
        socket still open. Breaking it instead keeps the membership and hands
        the client a reason to reload.
        """
        self._break_channel(channel, "a message expired before it could be sent", None)

    # ------------------------------------------------------------------
    # Breaking a channel
    # ------------------------------------------------------------------

    def _break_channel(self, channel: str, reason: str, message: dict | None) -> None:
        """Flush the channel and leave a single ``resync_required`` behind."""
        if channel in self._broken:
            self._count_discard(channel)
            return

        self._broken.add(channel)
        self._discarded[channel] = 0
        self._last_log_at[channel] = time.time()

        logger.warning(
            "WebSocket updates lost for channel %s: %s (dropped %s). Its queue "
            "was flushed and a %s frame left in its place; further messages are "
            "discarded until the client reconnects.",
            channel, reason, _message_type(message) or "a frame",
            self.RESYNC_MESSAGE_TYPE,
        )

        queue = self.channels.get(channel)
        if queue is None:
            # Nothing queued yet — the next send is a no-op, and the client
            # will be told when it reconnects. Nothing to flush or inject.
            return

        while not queue.empty():
            queue.get_nowait()

        # math.inf, not the usual TTL: this frame is the client's only way to
        # learn it is out of sync. Letting it expire would put the channel back
        # in the silent state this whole class exists to remove.
        queue.put_nowait((math.inf, {
            "type": "broadcast",
            "data": {"type": self.RESYNC_MESSAGE_TYPE, "reason": reason},
        }))

    def _count_discard(self, channel: str) -> None:
        """Tally a message dropped for an already-broken channel."""
        self._discarded[channel] = self._discarded.get(channel, 0) + 1

        now = time.time()
        last = self._last_log_at.get(channel, 0.0)
        if now - last < self.drop_log_window:
            return

        logger.warning(
            "WebSocket updates still lost for channel %s: %d further frames "
            "discarded in the last %.0fs.",
            channel, self._discarded[channel], now - last,
        )
        self._discarded[channel] = 0
        self._last_log_at[channel] = now

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def group_discard(self, group: str, channel: str) -> None:
        """Forget a channel once it belongs to no group at all.

        Called from the consumer's ``disconnect``. Dropping the queue matters
        for a broken channel: its resync frame never expires, so the base
        class's own cleanup (which runs when a queue empties) would never
        reclaim it.
        """
        await super().group_discard(group, channel)

        if any(channel in channels for channels in self.groups.values()):
            return  # still subscribed elsewhere

        self._broken.discard(channel)
        self._discarded.pop(channel, None)
        self._last_log_at.pop(channel, None)
        self.channels.pop(channel, None)


def _message_type(message: dict | None) -> str | None:
    """Best-effort ``data.type`` of a broadcast envelope, for logging only.

    Never the payload itself: a single ``session_items_added`` carries the full
    content of its items, which runs to megabytes.
    """
    if not isinstance(message, dict):
        return None
    data = message.get("data")
    if isinstance(data, dict) and isinstance(data.get("type"), str):
        return data["type"]
    kind = message.get("type")
    return kind if isinstance(kind, str) else None
