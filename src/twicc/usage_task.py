"""Cross-provider building blocks for usage sync flows.

Each provider's orchestrator owns the lifecycle of its own usage sync
task (start/stop, interval, error handling). What's reusable across
providers — fetching the latest snapshot, computing reference series,
shaping the wire payload, broadcasting — lives here. All entry points
are parameterised by ``provider`` (or take a ``UsageSnapshot`` whose
``provider`` field carries the scope).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.enums import Provider
from twicc.core.models import UsageSnapshot
from twicc.core.serializers import serialize_usage_snapshot
from twicc.synced_settings import read_synced_settings
from twicc.usage import compute_period_costs

# Per-provider previous "extra usage recently active" flag, used to fire the
# "extra usage started" alert on the rising edge (quiet → active). In-memory and
# reset on restart, exactly like external_notifications._last_seen: a restart
# re-seeds the baseline (the next observation never fires), so a process bounce
# can at worst miss one start event, never invent one. Keyed by provider value.
_extra_usage_prev_active: dict[str, bool] = {}


@sync_to_async
def _get_latest_usage_snapshot(provider: Provider) -> UsageSnapshot | None:
    """Return the most recent :class:`UsageSnapshot` for ``provider``, or ``None``."""
    return UsageSnapshot.objects.filter(provider=provider.value).first()  # ordered by -fetched_at


def _build_reference_snapshots(snapshot: UsageSnapshot) -> dict | None:
    """Query and serialize reference snapshots for recent burn rate computation.

    Looks up historical snapshots at four lookback targets:
    - ~1 hour and ~30 minutes ago (for the 5-hour window)
    - ~24 hours and ~12 hours ago (for the 7-day window)

    Intra-period references are constrained to the **current quota window**
    (after its start time) to avoid crossing a reset boundary.

    When the current window is younger than a lookback interval, also
    provides **cross-period** references (``cross_fh_long``,
    ``cross_fh_short``, ``cross_sd_long``, ``cross_sd_short``), each with
    ``prev_ref`` and ``prev_end`` snapshots from the previous period. The
    frontend uses these to compute a meaningful recent burn rate even in
    the early minutes of a new period.

    All queries are scoped to ``snapshot.provider`` so multi-provider
    deployments don't mix reference series across providers.

    Returns a dict with reference keys, or ``None`` if no references are
    available.
    """
    if not snapshot or not snapshot.fetched_at:
        return None

    now = snapshot.fetched_at
    refs: dict = {}

    def _fmt_dt(dt):
        return dt.isoformat() if dt else None

    def _find_ref(target_delta: timedelta, window_start: datetime | None) -> UsageSnapshot | None:
        """Find the oldest snapshot within the lookback target and current window.

        The floor is the later of (now - target_delta) and window_start,
        so the delta never exceeds the target and never crosses a reset.
        """
        floor = now - target_delta
        if window_start and window_start > floor:
            floor = window_start
        return (
            UsageSnapshot.objects
            .filter(provider=snapshot.provider)
            .exclude(pk=snapshot.pk)
            .filter(fetched_at__gte=floor)
            .order_by("fetched_at")
            .first()
        )

    # 5h window start (from resets_at - 5h)
    fh_window_start = (
        snapshot.five_hour_resets_at - timedelta(hours=5)
        if snapshot.five_hour_resets_at
        else None
    )

    # 7d window start (from resets_at - 7d)
    sd_window_start = (
        snapshot.seven_day_resets_at - timedelta(days=7)
        if snapshot.seven_day_resets_at
        else None
    )

    def _serialize_fh_ref(key: str, ref: UsageSnapshot | None) -> None:
        if ref:
            refs[key] = {
                "fetched_at": _fmt_dt(ref.fetched_at),
                "five_hour_utilization": ref.five_hour_utilization,
            }

    def _serialize_sd_ref(key: str, ref: UsageSnapshot | None) -> None:
        if ref:
            refs[key] = {
                "fetched_at": _fmt_dt(ref.fetched_at),
                "seven_day_utilization": ref.seven_day_utilization,
            }

    def _serialize_extra_usage_ref(key: str, ref: UsageSnapshot | None) -> None:
        # Used by the frontend to detect "recent activity" on the extra-usage
        # block (e.g. fast mode consuming extra credits even before the
        # standard quotas saturate). Unconstrained by quota windows: extra
        # usage resets on a different (monthly) cadence than 5h/7d.
        if ref:
            refs[key] = {
                "fetched_at": _fmt_dt(ref.fetched_at),
                "extra_usage_utilization": ref.extra_usage_utilization,
                "extra_usage_used_credits": ref.extra_usage_used_credits,
                "extra_usage_remaining_credits": ref.extra_usage_remaining_credits,
            }

    # References for 5h window: 1h and 30min lookbacks
    _serialize_fh_ref("one_hour", _find_ref(timedelta(hours=1), fh_window_start))
    _serialize_fh_ref("thirty_min", _find_ref(timedelta(minutes=30), fh_window_start))

    # References for 7d windows: 24h and 12h lookbacks
    _serialize_sd_ref("one_day", _find_ref(timedelta(hours=24), sd_window_start))
    _serialize_sd_ref("twelve_hour", _find_ref(timedelta(hours=12), sd_window_start))

    # Reference for the extra-usage recent-activity gate (1h lookback, not
    # tied to a quota window since extra usage resets on a different cadence).
    _serialize_extra_usage_ref("extra_usage_one_hour", _find_ref(timedelta(hours=1), None))

    # Cross-period references: when the current window is younger than the lookback,
    # we look into the previous period to compute a meaningful recent burn rate.
    # For each lookback, we provide two snapshots from the previous period:
    #   prev_ref: closest to (now - lookback), to measure old-period consumption
    #   prev_end: last snapshot before the current window started
    def _find_cross_period(window_start, lookback, resets_at_field):
        if window_start is None:
            return None
        elapsed = (now - window_start).total_seconds()
        if elapsed >= lookback.total_seconds():
            return None  # enough intra-period data

        target = now - lookback
        # Oldest snapshot in [target, window_start)
        prev_ref = (
            UsageSnapshot.objects
            .filter(provider=snapshot.provider)
            .filter(fetched_at__gte=target, fetched_at__lt=window_start)
            .order_by("fetched_at")
            .first()
        )
        if not prev_ref:
            return None

        # Most recent snapshot before window_start that still belongs to the
        # previous period (resets_at is not null — excludes the gap between
        # periods where the API reports utilization=0 with resets_at=None).
        prev_end = (
            UsageSnapshot.objects
            .filter(provider=snapshot.provider)
            .filter(fetched_at__lt=window_start, **{f"{resets_at_field}__isnull": False})
            .order_by("-fetched_at")
            .first()
        )
        if not prev_end:
            return None

        return prev_ref, prev_end

    def _serialize_fh_cross(key, window_start, lookback):
        result = _find_cross_period(window_start, lookback, "five_hour_resets_at")
        if result:
            prev_ref, prev_end = result
            refs[key] = {
                "prev_ref": {"fetched_at": _fmt_dt(prev_ref.fetched_at), "five_hour_utilization": prev_ref.five_hour_utilization},
                "prev_end": {"fetched_at": _fmt_dt(prev_end.fetched_at), "five_hour_utilization": prev_end.five_hour_utilization},
            }

    def _serialize_sd_cross(key, window_start, lookback):
        result = _find_cross_period(window_start, lookback, "seven_day_resets_at")
        if result:
            prev_ref, prev_end = result
            def _sd_fields(snap):
                return {
                    "fetched_at": _fmt_dt(snap.fetched_at),
                    "seven_day_utilization": snap.seven_day_utilization,
                }
            refs[key] = {"prev_ref": _sd_fields(prev_ref), "prev_end": _sd_fields(prev_end)}

    _serialize_fh_cross("cross_fh_long", fh_window_start, timedelta(hours=1))
    _serialize_fh_cross("cross_fh_short", fh_window_start, timedelta(minutes=30))
    _serialize_sd_cross("cross_sd_long", sd_window_start, timedelta(hours=24))
    _serialize_sd_cross("cross_sd_short", sd_window_start, timedelta(hours=12))

    return refs if refs else None


def _build_usage_message(
    provider: Provider, success: bool, reason: str, snapshot: UsageSnapshot | None
) -> dict:
    """Build a ``usage_updated`` message payload for ``provider``.

    ``provider`` is exposed at the top level of the payload so the
    frontend can route the message even when ``snapshot`` is ``None``
    (failure case: no snapshot yet, but we still want the failure
    associated with the right provider).

    Includes period cost data (spent, estimated_period, estimated_monthly)
    for the 5-hour and 7-day windows when a snapshot is available.
    """
    if snapshot:
        period_costs = compute_period_costs(snapshot)
        references = _build_reference_snapshots(snapshot)
        usage = serialize_usage_snapshot(snapshot, period_costs=period_costs, references=references)
    else:
        usage = None

    return {
        "type": "usage_updated",
        "provider": provider.value,
        "success": success,
        "reason": reason,  # "sync" = after API fetch, "connection" = on WS connect
        "usage": usage,
    }


@sync_to_async
def _build_usage_message_sync(
    provider: Provider, success: bool, reason: str, snapshot: UsageSnapshot | None
) -> dict:
    """``sync_to_async`` wrapper for :func:`_build_usage_message`.

    Required because :func:`compute_period_costs` and
    :func:`_build_reference_snapshots` perform database queries that
    cannot run in an async context.
    """
    return _build_usage_message(provider, success, reason, snapshot)


def _find_extra_usage_ref(snapshot: UsageSnapshot) -> UsageSnapshot | None:
    """Return the ~1h-ago snapshot used to detect recent extra-usage activity.

    Unconstrained by the 5h/7d quota windows: extra usage resets on a different
    (monthly) cadence, so the lookback is a plain 1-hour floor. Mirrors the
    ``extra_usage_one_hour`` reference the broadcast already serializes for the
    frontend (see ``_build_reference_snapshots``).
    """
    floor = snapshot.fetched_at - timedelta(hours=1)
    return (
        UsageSnapshot.objects
        .filter(provider=snapshot.provider)
        .exclude(pk=snapshot.pk)
        .filter(fetched_at__gte=floor)
        .order_by("fetched_at")
        .first()
    )


def _extra_usage_recently_active(snapshot: UsageSnapshot, ref: UsageSnapshot | None) -> bool:
    """Whether extra usage was consumed since ``ref`` (~1h ago).

    Python mirror of the frontend ``computeExtraUsageRecentlyActive`` (usage.js):
    Anthropic-style providers report a rising ``utilization`` as credits are
    spent; Codex-style providers report a falling remaining balance. Conservative
    when the reference is missing (returns ``False`` rather than risk a false
    positive). Keep this in sync with the JS definition.
    """
    if not ref:
        return False
    cur_util = snapshot.extra_usage_utilization
    if cur_util is not None and cur_util > (ref.extra_usage_utilization or 0):
        return True
    cur_rem = snapshot.extra_usage_remaining_credits
    ref_rem = ref.extra_usage_remaining_credits
    if cur_rem is not None and ref_rem is not None and cur_rem < ref_rem:
        return True
    return False


@sync_to_async
def _evaluate_extra_usage_start(provider: Provider, snapshot: UsageSnapshot) -> dict | None:
    """Decide whether to fire the "extra usage started" alert for this tick.

    Runs all the blocking work (the 1h-ref DB query, the synced-settings read)
    in one ``sync_to_async`` block and returns the synced-settings dict when the
    alert should fire, or ``None`` otherwise. Updates the per-provider baseline
    on every call so the rising edge is detected exactly once.

    Returns ``None`` (no alert) when: the provider has no extra usage enabled,
    there is no rising edge (first observation seeds the baseline silently, or
    consumption was already active), or the master ``notifyOnExtraUsageStart``
    switch is off (the global kill switch — suppresses every channel).
    """
    active = bool(
        snapshot.extra_usage_is_enabled
        and _extra_usage_recently_active(snapshot, _find_extra_usage_ref(snapshot))
    )
    prev = _extra_usage_prev_active.get(provider.value)
    _extra_usage_prev_active[provider.value] = active
    # Rising edge only: a known previous ``False`` flipping to ``True``.
    # ``None`` (first observation) or ``True`` (already active) never fire.
    if prev is not False or not active:
        return None
    settings = read_synced_settings()
    if not settings.get("notifyOnExtraUsageStart", True):
        return None
    return settings


def _extra_usage_event_payload(snapshot: UsageSnapshot) -> dict:
    """The ``extra_usage`` block carried by the ``extra_usage_started`` WS event.

    Snake-case to match the rest of the usage wire shape; the frontend formats
    the credit detail (used/limit vs remaining) from these fields.
    """
    return {
        "utilization": snapshot.extra_usage_utilization,
        "used_credits": snapshot.extra_usage_used_credits,
        "monthly_limit": snapshot.extra_usage_monthly_limit,
        "remaining_credits": snapshot.extra_usage_remaining_credits,
    }


async def _maybe_notify_extra_usage_started(provider: Provider, snapshot: UsageSnapshot | None) -> None:
    """Fire the "extra usage started" alert on the rising edge, if the master switch is on.

    Fans out to two channels: a ``extra_usage_started`` WS event (drives the
    in-app toast plus the per-device sound/browser notifications) and the Apprise
    external push (per-target opt-in). Both are gated by the single master switch
    evaluated in :func:`_evaluate_extra_usage_start`.
    """
    if not snapshot:
        return
    settings = await _evaluate_extra_usage_start(provider, snapshot)
    if settings is None:
        return
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "extra_usage_started",
                "provider": provider.value,
                "extra_usage": _extra_usage_event_payload(snapshot),
            },
        },
    )
    # External push (per-target opt-in, away-only/presence handled inside). Sync
    # fire-and-forget like the process-state path; never raises.
    from twicc import external_notifications
    external_notifications.notify_extra_usage_started(provider, snapshot, settings)


async def broadcast_usage_updated(provider: Provider, success: bool, reason: str = "sync") -> None:
    """Broadcast a ``usage_updated`` message for ``provider`` to all connected clients.

    Always sends the latest snapshot for that provider from the database
    (not necessarily the one just fetched) when one exists, plus a
    ``success`` flag indicating whether the last fetch succeeded. The
    frontend uses the snapshot's ``fetched_at`` to flag stale data.

    ``reason`` defaults to ``"sync"`` (periodic background fetch); the
    user-initiated refresh path passes ``"manual"`` so the frontend can
    tell its on-demand refresh round-trip apart from a background tick.
    """
    snapshot = await _get_latest_usage_snapshot(provider)
    data = await _build_usage_message_sync(provider, success, reason=reason, snapshot=snapshot)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": data,
        },
    )
    # Detect the "extra usage started" rising edge and, if the master switch is
    # on, fan out to the in-app alert (WS event) and the external push.
    await _maybe_notify_extra_usage_started(provider, snapshot)


async def get_usage_message_for_connection(provider: Provider) -> dict:
    """Build a ``usage_updated`` message for ``provider`` for a single client on WS connect.

    Returns the latest snapshot for that provider with ``reason="connection"``,
    or a message with no usage data if no snapshot exists yet.
    """
    snapshot = await _get_latest_usage_snapshot(provider)
    return await _build_usage_message_sync(provider, success=True, reason="connection", snapshot=snapshot)
