"""Tests for the telemetry background task (design docs/plans/2026-07-18-telemetry-design.md).

No network: httpx is always monkeypatched. ``is_telemetry_active`` gating uses
the ``temp_settings`` fixture pattern from ``tests/test_settings_mutation.py``
(a tmp settings.json, module cache cleared) so tests never touch the real
synced settings file.
"""

import asyncio

import httpx
import pytest

import twicc.synced_settings as ss
from twicc.telemetry import task
from twicc.telemetry.state import utc_today


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.fixture
def temp_state(tmp_path, monkeypatch):
    """Point the telemetry state file at a tmp path, isolated from the real data dir."""
    import twicc.telemetry.state as state_mod

    state_path = tmp_path / "telemetry.json"
    monkeypatch.setattr(state_mod, "get_state_path", lambda: state_path)
    return state_path


class TestIsTelemetryActive:
    def test_false_when_env_kill_switch_disabled(self, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", False)
        assert task.is_telemetry_active() is False

    def test_false_when_synced_setting_disabled(self, temp_settings, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": False})
        assert task.is_telemetry_active() is False

    def test_true_otherwise(self, temp_settings, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        # Isolated synced settings with no override -> the default
        # telemetryEnabled (True) applies, so telemetry is active.
        assert task.is_telemetry_active() is True

    def test_true_when_synced_setting_is_null(self, temp_settings, monkeypatch):
        # The frontend syncs a `null` placeholder for unset synced keys; a
        # present null must read as enabled (default-on), matching the frontend
        # getter `telemetryEnabled !== false` -- never silently disabled.
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": None})
        assert task.is_telemetry_active() is True


class TestSendCycle:
    def test_no_pending_payload_no_post_attempted(self, monkeypatch):
        monkeypatch.setattr(task, "build_pending_payload", lambda: None)

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("httpx.AsyncClient should not be instantiated")

        monkeypatch.setattr(task.httpx, "AsyncClient", _fail_if_called)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []

    def test_unsent_day_posted_and_marker_advanced_on_success(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10", "messages_sent": 3}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        posts = []

        class _FakeResponse:
            status_code = 204

            def raise_for_status(self):
                pass

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                posts.append((url, json))
                return _FakeResponse()

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert len(posts) == 1
        url, body = posts[0]
        assert url == task.settings.TELEMETRY_ENDPOINT
        assert body == payload
        assert [d["date"] for d in body["days"]] == ["2026-07-10"]
        assert mark_sent_calls == [(("2026-07-10", payload), {})]

    def test_network_error_leaves_marker_untouched(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10"}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                raise ConnectionError("network unreachable")

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []

    def test_http_status_error_leaves_marker_untouched(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10"}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        class _FakeResponse:
            status_code = 500

            def raise_for_status(self):
                request = httpx.Request("POST", task.settings.TELEMETRY_ENDPOINT)
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError("Server error", request=request, response=response)

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                return _FakeResponse()

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []


@pytest.mark.django_db
class TestTickOnceGating:
    def test_tick_once_noop_when_inactive(self, temp_state, temp_settings, monkeypatch):
        # First tick while active: records a real day entry. temp_settings
        # isolates the synced settings so is_telemetry_active() sees the default
        # (telemetryEnabled unset -> True) instead of the real machine's file.
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        task.tick_once()

        import twicc.telemetry.state as state_mod

        day = utc_today().isoformat()
        state_after_first_tick = state_mod.ensure_state()
        assert day in state_after_first_tick["days"]
        entry_after_first_tick = dict(state_after_first_tick["days"][day])

        # Disable via the env kill switch, tick again: no change.
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", False)
        task.tick_once()

        state_after_second_tick = state_mod.ensure_state()
        assert state_after_second_tick["days"][day] == entry_after_first_tick


class TestStartTelemetryTaskLoop:
    """Drives the actual loop in ``start_telemetry_task`` -- ``tick_once``/``send_cycle``
    are replaced with counting spies so no DB/network is touched; ``TICK_INTERVAL``/
    ``TELEMETRY_SEND_INTERVAL`` are shrunk so the whole run takes a few milliseconds.
    The tick spy sets the stop event once it has been called enough times, so the
    assertions are driven by call counts, never by wall-clock timing.
    """

    def test_sends_on_first_iteration_then_waits_a_full_interval(self, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 0.003)  # 3 ticks between sends

        stop_event = asyncio.Event()
        tick_calls: list[int] = []
        send_calls: list[int] = []  # tick count at the time each send fired

        def fake_tick_once():
            tick_calls.append(len(tick_calls) + 1)
            if len(tick_calls) >= 4:
                stop_event.set()

        async def fake_send_cycle():
            send_calls.append(len(tick_calls))

        monkeypatch.setattr(task, "tick_once", fake_tick_once)
        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        asyncio.run(asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5))

        # Sent once right on the first iteration (ticks_since_send starts already
        # "due"), then not again until a full TELEMETRY_SEND_INTERVAL worth of
        # ticks (3) had elapsed -- i.e. on the 4th tick, not before.
        assert tick_calls == [1, 2, 3, 4]
        assert send_calls == [1, 4]

    def test_exits_promptly_once_stop_event_is_set(self, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 10)  # never due, keep it out of the way

        stop_event = asyncio.Event()
        tick_calls: list[int] = []

        def fake_tick_once():
            tick_calls.append(1)
            if len(tick_calls) >= 2:
                stop_event.set()

        async def fake_send_cycle():
            raise AssertionError("send_cycle should not fire in this test")

        monkeypatch.setattr(task, "tick_once", fake_tick_once)
        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        # A generous bound: proves the loop actually exits once the event is
        # set, rather than spinning until the timeout.
        asyncio.run(asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5))

        assert len(tick_calls) == 2

    def test_cancellation_propagates_and_does_not_hang(self, monkeypatch):
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", True)
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 10)

        monkeypatch.setattr(task, "tick_once", lambda: None)

        async def fake_send_cycle():
            return None

        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        async def _run():
            stop_event = asyncio.Event()
            task_obj = asyncio.create_task(task.start_telemetry_task(stop_event))
            await asyncio.sleep(0.01)  # let it enter the loop for a few iterations
            task_obj.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task_obj, timeout=5)

        asyncio.run(_run())

    def test_early_return_when_env_disabled(self, temp_state, monkeypatch):
        # temp_state: the kill-switched early return records was_active=False
        # in the state file, which must not be the real one.
        monkeypatch.setattr(task.settings, "TELEMETRY_ENABLED", False)

        tick_calls: list[int] = []
        monkeypatch.setattr(task, "tick_once", lambda: tick_calls.append(1))

        async def fake_send_cycle():
            raise AssertionError("send_cycle should not fire when telemetry is disabled")

        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        async def _run():
            stop_event = asyncio.Event()
            await asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5)

        asyncio.run(_run())

        assert tick_calls == []
