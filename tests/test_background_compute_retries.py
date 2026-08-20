import asyncio

from twicc.providers import background_compute_task

run_compute_with_stale_retries = background_compute_task.run_compute_with_stale_retries


def test_stale_compute_retries_only_a_session_that_stayed_quiet():
    pass_calls = []
    remaining_results = [["active"], []]
    offset_results = [
        {"active": 12},
        {"active": 12},
    ]
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return remaining_results.pop(0)

    async def load_offsets(_session_ids):
        return offset_results.pop(0)

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ["ready", "active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
    ))

    assert pass_calls == [["ready", "active"], ["active"]]
    assert delays == [1.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == []


def test_stale_compute_stops_after_three_changing_windows():
    pass_calls = []
    offsets = iter((10, 11, 12, 13, 14, 15))
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return ["active"]

    async def load_offsets(_session_ids):
        return {"active": next(offsets)}

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ["active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
    ))

    assert pass_calls == [["active"]]
    assert delays == [1.0, 2.0, 4.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == ["active"]


def test_stale_compute_does_not_retry_a_failed_pass():
    remaining_calls = 0

    async def run_pass(_session_ids):
        return 1

    async def load_remaining(_session_ids):
        nonlocal remaining_calls
        remaining_calls += 1
        return ["failed"]

    async def load_offsets(_session_ids):
        raise AssertionError("offsets must not be loaded after a failed pass")

    async def wait_for_delay(_stop_event, _delay):
        raise AssertionError("a failed pass must not wait for a retry")

    result = asyncio.run(run_compute_with_stale_retries(
        ["failed"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
    ))

    assert remaining_calls == 0
    assert result.failed_count == 1
    assert result.remaining_session_ids == ["failed"]


def test_stale_compute_recovery_keeps_monitoring_until_the_session_is_quiet():
    pass_calls = []
    offsets = iter((10, 11, 12, 13, 14, 15, 16, 16))
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return [] if pass_calls else ["active"]

    async def load_offsets(_session_ids):
        return {"active": next(offsets)}

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(background_compute_task.recover_stale_compute_sessions(
        ["active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
        recovery_delays=(15.0, 30.0),
        recovery_interval=60.0,
    ))

    assert pass_calls == [["active"]]
    assert delays == [15.0, 30.0, 60.0, 60.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == []
