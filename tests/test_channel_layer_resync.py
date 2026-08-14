"""TwiccChannelLayer — a lost update must never be silent.

``InMemoryChannelLayer`` discards messages three ways, none observable by the
client: a full queue, a message outliving ``expiry`` (which also unsubscribes
the channel from every group), and ``group_expiry`` evicting a perfectly
healthy channel a fixed time after it joined.

The subclass converts every one of those into a single ``resync_required``
frame the client can act on. These tests pin that conversion, the library
behaviours it defends against, and the sizing that switches the third off.
"""

import asyncio
import logging
import math

import pytest
from channels.layers import InMemoryChannelLayer
from django.conf import settings

from twicc.channel_layer import TwiccChannelLayer


RESYNC = TwiccChannelLayer.RESYNC_MESSAGE_TYPE


def _frame(n):
    return {"type": "broadcast", "data": {"type": "session_updated", "n": n}}


def _drain(layer, channel):
    """Every message currently queued for a channel, oldest first."""
    queue = layer.channels.get(channel)
    if queue is None:
        return []
    return [item[1] for item in list(queue._queue)]


def _types(messages):
    return [m["data"]["type"] for m in messages]


async def _subscribed(layer, capacity=3, **kwargs):
    layer = layer or TwiccChannelLayer(capacity=capacity, **kwargs)
    channel = await layer.new_channel()
    await layer.group_add("updates", channel)
    return layer, channel


# ---------------------------------------------------------------------------
# Mode 1 — a full queue
# ---------------------------------------------------------------------------


def test_full_queue_replaces_the_backlog_with_one_resync_frame():
    """The torn backlog is dropped: a later frame is meaningless without the
    one that was lost, so delivering it would show a half-applied state."""
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=3))
        for i in range(4):  # one more than the queue holds
            await layer.group_send("updates", _frame(i))
        return _drain(layer, channel)

    assert _types(asyncio.run(scenario())) == [RESYNC]


def test_the_resync_frame_carries_a_reason():
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))
        return _drain(layer, channel)[0]

    frame = asyncio.run(scenario())
    assert frame["type"] == "broadcast"
    assert frame["data"]["type"] == RESYNC
    assert "queue is full" in frame["data"]["reason"]


def test_a_broken_channel_receives_nothing_further():
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=2))
        for i in range(10):
            await layer.group_send("updates", _frame(i))
        return _drain(layer, channel)

    assert _types(asyncio.run(scenario())) == [RESYNC]


def test_breaking_one_channel_leaves_the_others_alone():
    """One slow client must not deafen the tab next to it."""
    async def scenario():
        layer = TwiccChannelLayer(capacity=2)
        slow = await layer.new_channel()
        healthy = await layer.new_channel()
        await layer.group_add("updates", slow)
        await layer.group_add("updates", healthy)

        for i in range(3):
            await layer.group_send("updates", _frame(i))
            # The healthy client keeps up; the slow one never drains.
            await layer.receive(healthy)

        await layer.group_send("updates", _frame(99))
        return _types(_drain(layer, slow)), await layer.receive(healthy)

    slow_queue, healthy_last = asyncio.run(scenario())
    assert slow_queue == [RESYNC]
    assert healthy_last["data"]["n"] == 99


# ---------------------------------------------------------------------------
# Mode 2 — an expired message
# ---------------------------------------------------------------------------


def test_library_unsubscribes_on_expiry():
    """The behaviour being defended against — pinned so an upgrade that changes
    it does not quietly make our override pointless."""
    async def scenario():
        layer, channel = await _subscribed(InMemoryChannelLayer(expiry=-1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))
        return channel in layer.groups.get("updates", {})

    assert asyncio.run(scenario()) is False


def test_expiry_keeps_the_subscription_and_signals_instead():
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(expiry=-1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))
        return channel in layer.groups.get("updates", {}), _types(_drain(layer, channel))

    still_subscribed, queued = asyncio.run(scenario())
    assert still_subscribed, "an expired message must not deafen the client"
    assert queued == [RESYNC]


def test_the_resync_frame_itself_never_expires():
    """It is the client's only way to learn it is out of sync. Letting it age
    out would restore the silence this class exists to remove."""
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(expiry=-1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))
        ttl = layer.channels[channel]._queue[0][0]

        # Several more cleanup passes: the frame must survive all of them.
        for i in range(3):
            await layer.group_send("updates", _frame(i))
        return ttl, _types(_drain(layer, channel))

    ttl, queued = asyncio.run(scenario())
    assert ttl == math.inf
    assert queued == [RESYNC]


# ---------------------------------------------------------------------------
# Mode 3 — group_expiry evicting a healthy channel
# ---------------------------------------------------------------------------


def test_library_evicts_a_healthy_channel_after_group_expiry():
    """The join timestamp is written once and never refreshed, so this is not
    an idle timeout: constant healthy traffic does not hold it off."""
    async def scenario():
        layer, channel = await _subscribed(InMemoryChannelLayer(group_expiry=86400))

        for i in range(5):  # busy, well-behaved client
            await layer.group_send("updates", _frame(i))
            await layer.receive(channel)
        joined_at = layer.groups["updates"][channel]

        layer.groups["updates"][channel] = joined_at - 86401  # 24h later
        await layer.group_send("updates", _frame(99))
        return channel in layer.groups.get("updates", {})

    assert asyncio.run(scenario()) is False


def test_configured_group_expiry_puts_that_eviction_out_of_reach():
    config = settings.CHANNEL_LAYERS["default"]["CONFIG"]
    assert config["group_expiry"] >= 365 * 24 * 3600


def test_settings_use_the_twicc_layer():
    assert settings.CHANNEL_LAYERS["default"]["BACKEND"] == "twicc.channel_layer.TwiccChannelLayer"


def test_config_reaches_the_instantiated_layer():
    """CONFIG is passed to the backend as kwargs — a rename or a stray nesting
    level would silently leave the library defaults in place."""
    config = settings.CHANNEL_LAYERS["default"]["CONFIG"]
    layer = TwiccChannelLayer(**config)

    assert layer.capacity == config["capacity"]
    assert layer.expiry == config["expiry"]
    assert layer.group_expiry == config["group_expiry"]


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_flushing_is_what_makes_the_signal_deliverable():
    """The queue that was full necessarily has room afterwards, so the frame
    never waits for the client to catch up."""
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=2))
        for i in range(5):
            await layer.group_send("updates", _frame(i))
        # A consumer asking for its next message gets the signal immediately.
        return await asyncio.wait_for(layer.receive(channel), timeout=1)

    assert asyncio.run(scenario())["data"]["type"] == RESYNC


def test_the_signal_is_the_next_thing_a_busy_consumer_sees():
    """A consumer breaks precisely because it is stuck sending, not receiving.
    When it finally comes back for its next message, the signal is waiting and
    nothing from the torn stream is ahead of it."""
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=3))
        for i in range(3):
            await layer.group_send("updates", _frame(i))  # queued while it is busy
        await layer.group_send("updates", _frame(3))      # overflows -> breaks

        first = await asyncio.wait_for(layer.receive(channel), timeout=1)
        return first, layer.channels.get(channel) is None

    first, drained = asyncio.run(scenario())
    assert first["data"]["type"] == RESYNC
    assert drained, "nothing else should be left behind it"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.fixture
def layer_logs():
    """Capture this module's records whatever the global logging state.

    ``caplog`` is not enough here: ``settings_test`` configures logging with
    ``disable_existing_loggers: True``, so whether our logger is still enabled
    depends on import order across the whole suite — these tests pass alone and
    fail in the suite. Owning the logger keeps the assertions about our code
    rather than about test ordering. Production is unaffected: the real
    ``LOGGING`` sets ``disable_existing_loggers: False`` and routes ``twicc``
    to the backend log file.
    """
    logger = logging.getLogger("twicc.channel_layer")
    was_disabled, was_level = logger.disabled, logger.level
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


def _rendered(records):
    return " ".join(r.getMessage() for r in records)


def test_the_first_loss_is_logged_with_its_cause(layer_logs):
    async def scenario():
        layer, _ = await _subscribed(TwiccChannelLayer(capacity=1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))

    asyncio.run(scenario())

    assert len(layer_logs) == 1
    assert "queue is full" in _rendered(layer_logs)
    assert RESYNC in _rendered(layer_logs)


def test_further_losses_are_counted_not_repeated(layer_logs):
    """A broken channel keeps receiving traffic; one line per loss would flood
    the log at streaming rates."""
    async def scenario():
        layer, _ = await _subscribed(TwiccChannelLayer(capacity=1, drop_log_window=3600))
        for i in range(200):
            await layer.group_send("updates", _frame(i))

    asyncio.run(scenario())

    assert len(layer_logs) == 1, "only the break itself should have been logged"


def test_the_tally_is_emitted_once_the_window_closes(layer_logs):
    async def scenario():
        layer, _ = await _subscribed(TwiccChannelLayer(capacity=1, drop_log_window=0))
        for i in range(4):
            await layer.group_send("updates", _frame(i))

    asyncio.run(scenario())

    assert len(layer_logs) > 1
    assert "further frames discarded" in _rendered(layer_logs)


def test_the_payload_is_never_logged(layer_logs):
    """A single session_items_added carries the full content of its items —
    megabytes in the real database."""
    secret = "x" * 5000
    heavy = {"type": "broadcast", "data": {"type": "session_items_added", "items": secret}}

    async def scenario():
        layer, _ = await _subscribed(TwiccChannelLayer(capacity=1))
        await layer.group_send("updates", heavy)
        await layer.group_send("updates", heavy)

    asyncio.run(scenario())

    rendered = _rendered(layer_logs)
    assert secret not in rendered
    assert "session_items_added" in rendered, "the type is useful and safe to log"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_a_reload_starts_from_a_clean_queue():
    """Disconnect discards the channel; the new connection gets a new name, so
    nothing of the broken stream survives into it."""
    async def scenario():
        layer, old = await _subscribed(TwiccChannelLayer(capacity=1))
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))  # breaks it

        await layer.group_discard("updates", old)     # consumer.disconnect()
        new = await layer.new_channel()
        await layer.group_add("updates", new)
        await layer.group_send("updates", _frame(2))

        return old == new, _types(_drain(layer, new)), old in layer.channels, old in layer._broken

    same_name, fresh_queue, old_kept, still_broken = asyncio.run(scenario())
    assert same_name is False
    assert fresh_queue == ["session_updated"]
    assert old_kept is False, "the broken queue must not linger — its frame never expires"
    assert still_broken is False


def test_state_survives_while_the_channel_is_still_in_a_group():
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=1))
        await layer.group_add("other", channel)
        await layer.group_send("updates", _frame(0))
        await layer.group_send("updates", _frame(1))

        await layer.group_discard("updates", channel)
        return channel in layer._broken

    assert asyncio.run(scenario()) is True


# ---------------------------------------------------------------------------
# Normal operation is untouched
# ---------------------------------------------------------------------------


def test_a_client_that_keeps_up_is_never_broken():
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=2))
        received = []
        for i in range(50):
            await layer.group_send("updates", _frame(i))
            received.append((await layer.receive(channel))["data"]["n"])
        return received, channel in layer._broken

    received, broken = asyncio.run(scenario())
    assert received == list(range(50))
    assert broken is False


@pytest.mark.parametrize("capacity", [1, 5, 100])
def test_the_queue_holds_exactly_its_capacity_before_breaking(capacity):
    async def scenario():
        layer, channel = await _subscribed(TwiccChannelLayer(capacity=capacity))
        for i in range(capacity):
            await layer.group_send("updates", _frame(i))
        return channel in layer._broken, layer.channels[channel].qsize()

    broken, size = asyncio.run(scenario())
    assert broken is False
    assert size == capacity
