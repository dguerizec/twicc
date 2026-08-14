"""Channel layer sizing — the two silent-loss modes of InMemoryChannelLayer.

Every live update reaches the browser through one ``group_send("updates", …)``
onto a per-consumer ``asyncio.Queue``. That queue discards in two ways, neither
of which raises or logs, so a regression here is invisible in production:

- past ``capacity``, ``group_send`` swallows ``ChannelFull`` and the frame is
  gone (a lost ``session_removed`` leaves a hidden session in the sidebar until
  the page is reloaded);
- past ``expiry``, ``_clean_expired`` drops the message *and* unsubscribes the
  channel from every group, so the client goes fully silent until it reconnects.

The behaviour tests pin the library semantics our sizing relies on; the settings
test pins the sizing itself.
"""

import asyncio

from channels.layers import InMemoryChannelLayer
from django.conf import settings


# ---------------------------------------------------------------------------
# The configured layer
# ---------------------------------------------------------------------------


def test_configured_layer_raises_both_defaults():
    """Our CONFIG must override both library defaults, not just one.

    Raising ``capacity`` alone would make the ``expiry`` eviction *more*
    likely: a deeper queue holds messages longer, so more of them reach the
    TTL and trigger the group removal.
    """
    config = settings.CHANNEL_LAYERS["default"]["CONFIG"]

    assert config["capacity"] > 100, "capacity must beat the library default"
    assert config["expiry"] > 60, "expiry must beat the library default"


def test_config_reaches_the_instantiated_layer():
    """``CONFIG`` is passed to the backend as kwargs — an unnoticed rename or a
    stray nesting level would silently leave the defaults in place."""
    config = settings.CHANNEL_LAYERS["default"]["CONFIG"]
    layer = InMemoryChannelLayer(**config)

    assert layer.capacity == config["capacity"]
    assert layer.expiry == config["expiry"]
    # get_capacity() is what send() actually consults for the queue's maxsize.
    assert layer.get_capacity("specific.inmemory!abc") == config["capacity"]


# ---------------------------------------------------------------------------
# Loss mode 1 — a full queue drops frames without raising
# ---------------------------------------------------------------------------


def test_group_send_drops_silently_beyond_capacity():
    """Past ``capacity``, extra frames vanish and ``group_send`` still succeeds."""
    async def scenario():
        layer = InMemoryChannelLayer(capacity=3)
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)

        # One more than the queue can hold; nothing raises.
        for i in range(4):
            await layer.group_send("updates", {"type": "broadcast", "n": i})

        received = [await layer.receive(channel) for _ in range(3)]
        return [m["n"] for m in received]

    # The 4th frame is gone for good — no exception, no log, no retry.
    assert asyncio.run(scenario()) == [0, 1, 2]


def test_capacity_of_1000_absorbs_a_streaming_burst():
    """The configured depth holds a full burst that the default 100 would clip."""
    async def scenario():
        layer = InMemoryChannelLayer(**settings.CHANNEL_LAYERS["default"]["CONFIG"])
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)

        for i in range(1000):
            await layer.group_send("updates", {"type": "broadcast", "n": i})

        # A session_removed arriving at the tail of the burst must still land.
        return layer.channels[channel].qsize()

    assert asyncio.run(scenario()) == 1000


# ---------------------------------------------------------------------------
# Loss mode 2 — an expired message unsubscribes the client entirely
# ---------------------------------------------------------------------------


def test_expired_message_evicts_the_channel_from_every_group():
    """This is the blackout mode ``expiry`` guards against.

    A negative expiry makes every queued message read as already stale, so the
    eviction is deterministic without sleeping.
    """
    async def scenario():
        layer = InMemoryChannelLayer(expiry=-1)
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)

        # Queued, then immediately stale.
        await layer.group_send("updates", {"type": "broadcast", "n": 0})
        assert channel in layer.groups["updates"]

        # Any later broadcast runs _clean_expired first, which drops the stale
        # message and rips the channel out of the group.
        await layer.group_send("updates", {"type": "broadcast", "n": 1})
        return layer.groups.get("updates", {})

    # Still connected, still in the group dict's place — but no longer a member,
    # so every subsequent broadcast misses it. Nothing re-adds it: the consumer
    # only calls group_add() on connect.
    assert asyncio.run(scenario()) == {}
