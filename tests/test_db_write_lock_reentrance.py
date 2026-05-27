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
    _db_write_lock_lease,
    _run_under_db_write_lock,
    run_under_db_write_lock,
    spawn_isolated_db_write_task,
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
    """``_run_under_db_write_lock`` shares the same lease, so a nested
    call from inside an outer internal runner is safe too.
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
# Lease lifecycle — installed on acquire, invalidated on release
# ---------------------------------------------------------------------------


def test_lease_resets_after_success():
    """After a clean run, the outer's lease must be cleared from the
    caller's context.
    """

    async def scenario():
        assert _db_write_lock_lease.get() is None
        await run_under_db_write_lock(lambda: _identity("ok"))
        assert _db_write_lock_lease.get() is None

    asyncio.run(scenario())


def test_lease_resets_after_exception():
    """A factory that raises must still leave the caller's lease cleared."""

    class Boom(Exception):
        pass

    async def raiser():
        raise Boom()

    async def scenario():
        assert _db_write_lock_lease.get() is None
        with pytest.raises(Boom):
            await run_under_db_write_lock(lambda: raiser())
        assert _db_write_lock_lease.get() is None

    asyncio.run(scenario())


def test_lease_active_inside_factory():
    """The lease must read ``active == True`` while the factory is running."""

    seen: list[bool | None] = []

    async def factory():
        lease = _db_write_lock_lease.get()
        seen.append(None if lease is None else lease.active)

    async def scenario():
        await run_under_db_write_lock(lambda: factory())

    asyncio.run(scenario())
    assert seen == [True]


def test_lease_invalidated_after_release():
    """``finally`` flips ``active`` to ``False`` — this is what protects
    a sub-task that inherited the lease but only runs post-release from
    skipping the acquire and writing lock-less.
    """

    captured_lease: list[db_writer._LockLease] = []

    async def factory():
        lease = _db_write_lock_lease.get()
        assert lease is not None
        captured_lease.append(lease)
        assert lease.active is True

    async def scenario():
        await run_under_db_write_lock(lambda: factory())
        # After the outer returns, the captured lease must be invalidated
        # so any sub-task that inherited it falls through to acquire.
        assert captured_lease[0].active is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Independent serialisation — concurrent Tasks queue FIFO on the lock
# ---------------------------------------------------------------------------


def test_independent_tasks_serialise_fifo():
    """Two independent Tasks (each acquires fresh — neither inherits an
    active lease) must run serially under the lock, in submission order.
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

        # Spawn A as a top-level task — it does NOT inherit any lease
        # (none is installed), so it acquires the lock.
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
# Sub-task isolation — spawn_isolated_db_write_task opts out
# ---------------------------------------------------------------------------


def test_spawn_isolated_lets_subtask_acquire_independently():
    """A sub-task spawned with ``spawn_isolated_db_write_task()`` does
    NOT inherit the outer's lease, so it queues independently in the
    lock's FIFO behind the outer call.
    """

    order: list[str] = []

    async def sub_writer():
        # When this Task runs, the isolated context means we see no lease.
        # Without the isolation, we'd see the outer's lease and skip.
        assert _db_write_lock_lease.get() is None, "sub inherited lease"
        # The body that records progress runs INSIDE the lock so the
        # ordering assertions below witness real serialisation.
        await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))

    async def outer_factory():
        order.append("outer:enter")
        lease = _db_write_lock_lease.get()
        assert lease is not None and lease.active is True
        sub = spawn_isolated_db_write_task(sub_writer())
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


def test_naive_subtask_queues_fifo_behind_outer():
    """A sub-task spawned WITHOUT isolation inherits the lease but its
    Task is NOT in ``lease.drive_tasks`` — its reentrance check fails
    and it falls through to a real acquire. The acquire blocks until
    the outer releases, so the sub runs AFTER the outer factory has
    returned, FIFO behind us. This is the strict serialisation the
    drive_tasks restriction gives us; the previous "parallel-during-
    hold" footgun is now gone.

    Concretely: the sub records ``sub:acquired`` AFTER the outer's
    ``outer:exit``, demonstrating it did not run inside the outer's
    hold despite inheriting the lease.
    """

    order: list[str] = []

    async def sub_writer():
        # The lease was inherited but our current_task() is not in
        # lease.drive_tasks, so the reentrance check fails — we take
        # the real acquire path.
        await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))

    async def outer_factory():
        order.append("outer:enter")
        # Spawn sub WITHOUT isolation. It inherits the lease, attempts
        # acquire, blocks because we still hold the lock.
        sub = asyncio.create_task(sub_writer())
        # Yield so the sub gets a chance to attempt the acquire (and
        # block).
        await asyncio.sleep(0.05)
        # Sub must NOT have run its body — it is waiting for us to
        # release. If reentrance leaked, "sub:acquired" would already
        # be in order.
        assert "sub:acquired" not in order, (
            f"naive sub ran before outer released: {order}"
        )
        order.append("outer:exit")
        return sub

    async def scenario():
        sub = await run_under_db_write_lock(lambda: outer_factory())
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    assert order == ["outer:enter", "outer:exit", "sub:acquired"]


# NOTE: there is no test for the "factory awaits a naive sub-task
# from inside a locked block" pattern. With the drive_tasks restriction
# the sub blocks on its own acquire (we hold the lock), and the factory
# blocks on awaiting the sub. The result is a TRUE deadlock that
# ``asyncio.wait_for`` cannot break — the shield-loop inside
# :func:`_drive_inner_under_held_lock` deliberately waits for the
# inner Task to finish before yielding to a cancellation, so the
# top-level cancel never reaches the sub. A test for that pattern
# would hang the test suite indefinitely. The docstring on
# :func:`run_under_db_write_lock` warns about this; the safer
# alternative is to await the sub coroutine directly (without
# ``create_task``), which becomes a transparent reentrant call.


def test_stale_lease_after_release_acquires_normally():
    """A sub-task spawned WITHOUT isolation whose body only resumes
    after the outer released the lock inherits the lease — but the
    runner flipped ``active = False`` in its ``finally``, so when the
    sub resumes it observes the invalidated lease and falls through to
    a normal acquire instead of writing lock-less. This is the fix for
    the v2 Codex finding #2.

    The scenario forces "resume after release" by gating the sub on an
    Event the scenario sets only **after** ``run_under_db_write_lock``
    has returned. This guarantees the runner's ``finally`` has run
    (invalidating the lease and releasing the lock) before the sub
    inspects the lease.

    Without this gating, asyncio's ready-queue ordering would let the
    sub run before the runner's drive_continuation — the sub was
    scheduled (via ``create_task``) earlier than the drive_continuation
    (which was only scheduled by the inner Task's completion callback).
    That earlier ordering is the documented parallel-during-hold case
    and is tested separately.
    """

    order: list[str] = []

    async def scenario():
        release_event = asyncio.Event()

        async def sub_writer():
            # Wait until the scenario tells us the outer has released
            # (i.e. the runner's ``finally`` has run, lock is released,
            # lease invalidated). Without this gate, we might run before
            # the drive_continuation and observe lease.active=True
            # (parallel-during-hold case).
            await release_event.wait()
            lease = _db_write_lock_lease.get()
            assert lease is not None, "lease somehow not inherited"
            assert lease.active is False, (
                "stale-inherited lease should have been invalidated by "
                "the outer's release — got active=True, which would mean "
                "the sub-task writes without holding the lock"
            )
            order.append("sub:before-acquire")
            # Without invalidation, this nested call would skip the
            # acquire and write lock-less. With invalidation, it
            # acquires normally.
            await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))
            order.append("sub:after-release")

        async def outer_factory():
            order.append("outer:enter")
            # Spawn the sub WITHOUT isolation — it inherits the active lease.
            sub = asyncio.create_task(sub_writer())
            order.append("outer:exit")
            return sub

        sub = await run_under_db_write_lock(lambda: outer_factory())
        # The outer has returned: ``finally`` ran (lease invalidated,
        # lock released). Now let the sub proceed.
        release_event.set()
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    assert order == [
        "outer:enter",
        "outer:exit",
        "sub:before-acquire",
        "sub:acquired",
        "sub:after-release",
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _identity(value):
    return value


async def _append_then(label, sink):
    sink.append(label)
