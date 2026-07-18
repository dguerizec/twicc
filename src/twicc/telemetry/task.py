"""Telemetry background task (design §5.1).

One loop, 60 s granularity: every tick accumulates presence/peak into the
state file; every TELEMETRY_SEND_INTERVAL (and once at startup) builds and
POSTs the pending payload. Failures are logged at debug and never raise
out of the loop. The enabled state is re-checked every tick, so toggling
the synced setting applies without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TICK_INTERVAL = 60
TELEMETRY_SEND_INTERVAL = 24 * 60 * 60
DEFAULT_ENDPOINT = "https://twicc-telemetry.twidi.com/v1/telemetry"


def get_endpoint() -> str:
    # Override for dev/E2E (e.g. a local `wrangler dev` collector).
    return os.environ.get("TWICC_TELEMETRY_URL", "").strip() or DEFAULT_ENDPOINT


def is_telemetry_active() -> bool:
    if not settings.TELEMETRY_ENABLED:
        return False
    from twicc.synced_settings import read_synced_settings

    # Default-on: only an explicit False disables. A present ``null`` (the
    # frontend syncs a null placeholder for unset synced keys) must read as
    # enabled, matching the frontend getter ``telemetryEnabled !== false`` --
    # otherwise a synced null would silently disable telemetry despite the
    # default-on intent.
    return read_synced_settings().get("telemetryEnabled") is not False


def tick_once() -> None:
    """Sync: one accumulator sample. Called in a thread."""
    from twicc.telemetry.state import note_active_transition

    active = is_telemetry_active()
    # Track the enabled state on every tick so an off->on transition advances
    # the last-sent marker: days elapsed while disabled are never sent.
    note_active_transition(active)
    if not active:
        return
    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun
    from twicc.presence import is_user_present
    from twicc.telemetry.state import record_tick

    live = ProcessRun.objects.exclude(state=AgentState.DEAD.value).count()
    record_tick(present=is_user_present(), live_agents=live)


def build_pending_payload() -> dict | None:
    """Sync: state + snapshot. Called in a thread."""
    if not is_telemetry_active():
        return None
    from twicc.telemetry.snapshot import build_payload
    from twicc.telemetry.state import ensure_state

    return build_payload(ensure_state())


async def send_cycle() -> None:
    payload = await asyncio.to_thread(build_pending_payload)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(get_endpoint(), json=payload)
            response.raise_for_status()
    except Exception as exc:
        logger.debug("Telemetry send failed (will retry next cycle): %s", exc)
        return
    from twicc.telemetry.state import mark_sent
    sent_through = payload["days"][-1]["date"]
    await asyncio.to_thread(mark_sent, sent_through, payload)


async def start_telemetry_task(stop_event: asyncio.Event) -> None:
    if not settings.TELEMETRY_ENABLED:
        logger.info("Telemetry disabled (TWICC_NO_TELEMETRY)")
        # Record the disabled state so a later start without the kill switch
        # counts as an off->on transition (the disabled window is never sent).
        from twicc.telemetry.state import note_active_transition

        await asyncio.to_thread(note_active_transition, False)
        return
    logger.info("Telemetry task started")
    ticks_since_send = TELEMETRY_SEND_INTERVAL  # send on first loop entry
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(tick_once)
                ticks_since_send += TICK_INTERVAL
                if ticks_since_send >= TELEMETRY_SEND_INTERVAL:
                    ticks_since_send = 0
                    await send_cycle()
            except Exception:
                logger.debug("Telemetry cycle failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TICK_INTERVAL)
            except asyncio.TimeoutError:
                pass
            else:
                break
    finally:
        logger.info("Telemetry task stopped")
