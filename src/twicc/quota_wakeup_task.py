"""Daily per-provider quota warm-up.

Every provider we drive (Claude Code, Codex) enforces a rolling **5-hour**
quota window that starts on the *first* request after the previous window
expired and resets exactly 5 hours later. The first request of the day
therefore anchors the whole cascade: fire it at 06:00 and the windows fall
on 06–11 / 11–16 / 16–21, so a 09:00–19:00 working day spans three windows
instead of the two it would get if the first window only opened at 09:00.

This task lets the user pick a daily wall-clock time, per provider, at which
TwiCC sends a tiny throwaway request to open that first window early. Each
provider exposes the time via its
:attr:`BaseProviderHelpers.QUOTA_WAKEUP_SETTING_KEY` synced-settings key
(``"HH:MM"``, empty = disabled) and the actual request via
:meth:`BaseProviderHelpers.warm_up_quota`.

Design decisions (kept deliberately simple):

- **Local wall clock.** The time is the server's local time (the user's
  machine), matched to the minute. No timezone configuration.
- **No idle gating.** Unlike the usage sync loops (``twicc.usage_task``), this
  must fire when *nobody* is around — its whole point is to run before the user
  starts working. It therefore mirrors ``twicc.pricing_task`` (a plain
  ``stop_event`` sleep), never the presence-gated usage loops.
- **No catch-up.** If TwiCC was not running at the target minute, nothing
  fires: a user who turns their machine on later simply opens the window
  themselves by starting to work. The minute-exact match implements this for
  free — a process that boots past the target minute never matches it.
- **Trigger rule.** When the target minute arrives we look at the latest
  ``five_hour_resets_at`` for the provider: if it is set **and in the future**
  a window is already running, so we skip; otherwise (unset, or in the past
  because the cached snapshot is stale and the window has since expired) we
  warm up.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from asgiref.sync import sync_to_async
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import UsageSnapshot
from twicc.logging_context import provider_log_context
from twicc.providers.helpers import BaseProviderHelpers, get_provider_helpers_registry
from twicc.providers.state import is_provider_enabled
from twicc.synced_settings import read_synced_settings

logger = logging.getLogger(__name__)


# How often the loop wakes to check whether a provider's configured warm-up
# minute has arrived. Short enough that a 60-second target minute is never
# missed while the process runs continuously, long enough to stay negligible
# (each tick is one in-memory settings read plus a handful of comparisons).
_TICK_INTERVAL = 30

# Per-provider date (server local wall clock) of the last warm-up we fired.
# Pure tick de-duplication: with a sub-minute tick two ticks can land in the
# same target minute, and this stops the second from firing a redundant
# warm-up. It is NOT the trigger condition — whether to actually warm up is
# decided fresh each time from ``five_hour_resets_at`` (see :func:`_should_warm_up`).
# In-memory and reset on restart, which is harmless: the reset-timestamp guard
# already prevents a redundant warm-up even right after a bounce.
_last_fired: dict[str, date] = {}


def _parse_hhmm(value: object) -> tuple[int, int] | None:
    """Parse a ``"HH:MM"`` setting into ``(hour, minute)``.

    Returns ``None`` when unset (empty string), not a string, or malformed —
    all of which mean "warm-up disabled for this provider".
    """
    if not value or not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


@sync_to_async
def _latest_five_hour_resets_at(provider: Provider) -> datetime | None:
    """Return the latest snapshot's ``five_hour_resets_at`` for ``provider``, or ``None``."""
    snapshot = UsageSnapshot.objects.filter(provider=provider.value).first()  # ordered by -fetched_at
    return snapshot.five_hour_resets_at if snapshot else None


async def _should_warm_up(provider: Provider) -> bool:
    """Whether a 5-hour window needs opening for ``provider`` right now.

    Skips only when a window is demonstrably still running: the latest
    ``five_hour_resets_at`` is set AND in the future. Unset (no window known)
    or in the past (the cached snapshot is stale and the window has since
    expired) both mean "warm up".
    """
    resets_at = await _latest_five_hour_resets_at(provider)
    return not (resets_at and resets_at > timezone.now())


async def _warm_up_provider(provider: Provider, helpers: BaseProviderHelpers) -> None:
    """Run the trigger rule for ``provider`` and warm up its quota if needed.

    The whole body runs inside :func:`provider_log_context` so every log record
    it emits — including those from ``warm_up_quota`` / the underlying SDK probe,
    since the ``ContextVar`` propagates across ``await`` within this task —
    carries the provider in the dedicated log column rather than repeating it in
    each message.
    """
    with provider_log_context(provider):
        if not await _should_warm_up(provider):
            logger.info("Quota warm-up: a 5-hour window is already active, skipping")
            return
        logger.info("Quota warm-up: opening the 5-hour window with a throwaway request")
        try:
            result = await helpers.warm_up_quota()
        except Exception as e:  # noqa: BLE001 — a warm-up failure must never strand the loop
            logger.warning("Quota warm-up failed: %s", e)
            return
        if result is True:
            logger.info("Quota warm-up: 5-hour window started")
        elif result is False:
            logger.warning("Quota warm-up: provider rejected the request (auth?) — window not started")
        else:
            logger.warning("Quota warm-up: inconclusive (timeout/network) — window may not be started")


async def _run_quota_wakeup_tick(now_local: datetime) -> None:
    """Fire warm-ups for any enabled provider whose configured minute is now.

    ``now_local`` is the server's local wall-clock time. Reads the synced
    settings fresh each tick so a changed time is picked up without a restart.
    """
    settings = read_synced_settings()
    today = now_local.date()
    for provider, helpers in get_provider_helpers_registry().items():
        key = helpers.QUOTA_WAKEUP_SETTING_KEY
        if not key:
            continue
        target = _parse_hhmm(settings.get(key))
        if target is None:
            continue
        if (now_local.hour, now_local.minute) != target:
            continue
        if not is_provider_enabled(provider):
            continue
        # Tick de-dup: a sub-minute tick can re-enter the same target minute.
        if _last_fired.get(provider.value) == today:
            continue
        _last_fired[provider.value] = today
        await _warm_up_provider(provider, helpers)


async def start_quota_wakeup_task(stop_event: asyncio.Event) -> None:
    """Periodic daily quota warm-up loop.

    Mirrors :func:`twicc.pricing_task.start_price_sync_task`: a plain
    ``stop_event`` sleep with no presence gating, since the warm-up must fire
    even when no human is present. Sleeps :data:`_TICK_INTERVAL` seconds
    between checks; on each wake it compares the local clock against every
    provider's configured warm-up minute. The first sleep happens before the
    first tick, so a boot exactly at the target minute still relies on the
    minute match rather than firing unconditionally at startup.
    """
    logger.info("Quota warm-up task started")
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_TICK_INTERVAL)
            except TimeoutError:
                pass  # interval elapsed — time to check
            else:
                break  # stop_event fired
            try:
                # The tick matches a user-configured local HH:MM, so it must read
                # the server's local wall clock, not UTC.
                await _run_quota_wakeup_tick(datetime.now())  # noqa: DTZ005
            except Exception as e:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.error("Quota warm-up tick failed: %s", e, exc_info=True)
    finally:
        logger.info("Quota warm-up task stopped")
