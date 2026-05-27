"""Tests for the DB write lock's reentrance behaviour (LOCK#2).

These tests exercise the runners in isolation from the rest of the DB
writer machinery (no queues, no consumer task, no Django ORM). They
manually install / uninstall the module-level lock and stop event so
each test starts from a clean slate.
"""

from __future__ import annotations

import asyncio

import pytest

from twicc.providers import db_writer
from twicc.providers.db_writer import (
    _db_write_lock_held,
    _run_under_db_write_lock,
    make_db_write_isolated_context,
    run_under_db_write_lock,
)


@pytest.fixture(autouse=True)
def _install_lock_state():
    """Install a fresh asyncio.Lock + dummy stop event for each test.

    The public runner checks ``_db_writer_stop_event`` at entry, so we
    install a real ``asyncio.Event`` that we never set — the check then
    passes as ``not is_set()``.
    """
    saved_lock = db_writer._db_write_lock
    saved_stop = db_writer._db_writer_stop_event
    db_writer._db_write_lock = asyncio.Lock()
    db_writer._db_writer_stop_event = asyncio.Event()
    try:
        yield
    finally:
        db_writer._db_write_lock = saved_lock
        db_writer._db_writer_stop_event = saved_stop


# ---------------------------------------------------------------------------
# Reentrance — nested calls reuse the held lock instead of self-deadlocking
# ---------------------------------------------------------------------------


def test_nested_run_under_db_write_lock_does_not_deadlock():
    """An outer ``run_under_db_write_lock`` whose factory itself calls
    ``run_under_db_write_lock`` must complete without deadlocking.
    """

    async def inner():
        return "inner"

    async def outer_factory():
        # Nested call inside the held lock.
        nested = await run_under_db_write_lock(lambda: inner())
        return f"outer:{nested}"

    async def scenario():
        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: outer_factory()),
            timeout=2.0,
        )

    result = asyncio.run(scenario())
    assert result == "outer:inner"


def test_nested_via_helper_does_not_deadlock():
    """The nested call may be hidden behind a helper that the outer
    factory just ``await``s — reentrance still kicks in.
    """

    async def helper():
        return await run_under_db_write_lock(lambda: _identity("via-helper"))

    async def scenario():
        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: helper()),
            timeout=2.0,
        )

    assert asyncio.run(scenario()) == "via-helper"


def test_internal_runner_is_reentrant_too():
    """``_run_under_db_write_lock`` shares the same reentrance flag,
    so a nested call from inside an outer internal runner is safe too.
    """

    async def scenario():
        async def factory():
            return await _run_under_db_write_lock(lambda: _identity("nested-internal"))

        return await asyncio.wait_for(
            _run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    assert asyncio.run(scenario()) == "nested-internal"


def test_public_inside_internal_and_vice_versa():
    """Cross-runner reentrance: an internal-runner outer with a public
    inner (and the symmetric case) must both work.
    """

    async def public_in_internal():
        async def factory():
            return await run_under_db_write_lock(lambda: _identity("pub-in-int"))

        return await asyncio.wait_for(
            _run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    async def internal_in_public():
        async def factory():
            return await _run_under_db_write_lock(lambda: _identity("int-in-pub"))

        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    assert asyncio.run(public_in_internal()) == "pub-in-int"
    assert asyncio.run(internal_in_public()) == "int-in-pub"


# ---------------------------------------------------------------------------
# Flag lifecycle — reset on success, reset on exception
# ---------------------------------------------------------------------------


def test_flag_resets_after_success():
    """After a clean run, the held-flag must be back to False."""

    async def scenario():
        assert _db_write_lock_held.get() is False
        await run_under_db_write_lock(lambda: _identity("ok"))
        assert _db_write_lock_held.get() is False

    asyncio.run(scenario())


def test_flag_resets_after_exception():
    """A factory that raises must still leave the held-flag back to False."""

    class Boom(Exception):
        pass

    async def raiser():
        raise Boom()

    async def scenario():
        assert _db_write_lock_held.get() is False
        with pytest.raises(Boom):
            await run_under_db_write_lock(lambda: raiser())
        assert _db_write_lock_held.get() is False

    asyncio.run(scenario())


def test_flag_true_inside_factory():
    """The flag must read True while the factory is running."""

    seen: list[bool] = []

    async def factory():
        seen.append(_db_write_lock_held.get())

    async def scenario():
        await run_under_db_write_lock(lambda: factory())

    asyncio.run(scenario())
    assert seen == [True]


# ---------------------------------------------------------------------------
# Independent serialisation — concurrent Tasks queue FIFO on the lock
# ---------------------------------------------------------------------------


def test_independent_tasks_serialise_fifo():
    """Two independent Tasks (each acquires fresh — neither inherits the
    held flag) must run serially under the lock, in submission order.
    """

    order: list[str] = []

    async def scenario():
        started_a = asyncio.Event()
        released_a = asyncio.Event()

        async def slow_a():
            order.append("a:enter")
            started_a.set()
            await released_a.wait()
            order.append("a:exit")

        async def fast_b():
            order.append("b:enter")
            order.append("b:exit")

        # Spawn A as a top-level task — it does NOT inherit any flag
        # (none is set), so it acquires the lock.
        ta = asyncio.create_task(run_under_db_write_lock(lambda: slow_a()))
        await started_a.wait()  # A holds the lock now.

        # Spawn B; it will queue FIFO behind A.
        tb = asyncio.create_task(run_under_db_write_lock(lambda: fast_b()))

        # Give B a chance to attempt acquire (it must block on A).
        await asyncio.sleep(0.05)
        assert order == ["a:enter"], f"B started before A released: {order}"

        # Release A; B should then run.
        released_a.set()
        await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)

    asyncio.run(scenario())
    assert order == ["a:enter", "a:exit", "b:enter", "b:exit"]


# ---------------------------------------------------------------------------
# Isolated context — opt-out from coalescing when spawning a sub-task
# ---------------------------------------------------------------------------


def test_isolated_context_lets_subtask_acquire_independently():
    """A sub-task spawned with ``make_db_write_isolated_context()`` does
    NOT inherit the held flag, so it queues independently in the lock's
    FIFO behind the outer call.
    """

    order: list[str] = []

    async def sub_writer():
        # When this Task runs, the isolated context means our flag is
        # False. Without the isolation, we'd see True (the parent's).
        assert _db_write_lock_held.get() is False, "sub inherited held flag"
        # The body that records progress runs INSIDE the lock so the
        # ordering assertions below witness real serialisation.
        await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))

    async def outer_factory():
        order.append("outer:enter")
        assert _db_write_lock_held.get() is True
        sub_ctx = make_db_write_isolated_context()
        # In the isolated context the flag is False.
        assert sub_ctx.run(_db_write_lock_held.get) is False
        sub = asyncio.create_task(sub_writer(), context=sub_ctx)
        # Yield so the sub task reaches its run_under_db_write_lock call
        # and blocks on the acquire (we still hold the lock).
        await asyncio.sleep(0.05)
        # Crucial check: the sub's body has NOT executed yet — it is
        # waiting for us to release. If isolation were broken, the sub
        # would have skipped the acquire and run in parallel, putting
        # "sub:acquired" in order already.
        assert "sub:acquired" not in order, (
            f"sub ran before outer released: {order}"
        )
        order.append("outer:exit")
        # Returning the sub handle so the scenario can await it.
        return sub

    async def scenario():
        sub = await run_under_db_write_lock(lambda: outer_factory())
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    assert order == ["outer:enter", "outer:exit", "sub:acquired"]


def test_default_context_subtask_inherits_and_coalesces():
    """A sub-task spawned WITHOUT isolation inherits ``True`` for the
    flag — its nested ``run_under_db_write_lock`` call therefore skips
    the acquire and runs IN PARALLEL with the outer factory. This is
    the documented footgun and the reason
    :func:`make_db_write_isolated_context` exists.
    """

    order: list[str] = []

    async def sub_writer():
        # The flag is inherited — we observe True even though we are
        # a separate Task, because create_task copied the parent's
        # contextvars.Context.
        assert _db_write_lock_held.get() is True
        # Nested call reuses the held lock — runs immediately, in
        # parallel with the outer.
        await run_under_db_write_lock(lambda: _append_then("sub", order))

    async def outer_factory():
        order.append("outer:enter")
        sub = asyncio.create_task(sub_writer())  # no context= -> inherits
        # Yield so the sub can run interleaved with us.
        await asyncio.sleep(0.05)
        order.append("outer:exit")
        return sub

    async def scenario():
        sub = await run_under_db_write_lock(lambda: outer_factory())
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    # "sub" appears BEFORE "outer:exit" because the sub ran in
    # parallel with the outer factory — under one logical lock.
    assert "sub" in order
    assert order.index("sub") < order.index("outer:exit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _identity(value):
    return value


async def _append_then(label, sink):
    sink.append(label)
