"""CLI implementation for the ``twicc usage`` subcommand."""

import sys

import orjson


def main() -> None:
    """Print the latest usage snapshot as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import UsageSnapshot
    from twicc.core.serializers import serialize_usage_snapshot
    from twicc.providers.claude_code.usage import compute_period_costs

    snapshot = UsageSnapshot.objects.first()  # ordered by -fetched_at
    if snapshot is None:
        print("Error: no usage snapshot available.", file=sys.stderr)
        sys.exit(1)

    period_costs = compute_period_costs(snapshot)
    data = serialize_usage_snapshot(snapshot, period_costs=period_costs)

    # Add derived values from model properties (snapshot in time, not live)
    def _fmt_dt(dt):
        return dt.isoformat() if dt else None

    def _round_pct(v):
        return round(v, 1) if v is not None else None

    def _round_rate(v):
        return round(v * 100, 1) if v is not None else None

    data["five_hour_temporal_pct"] = _round_pct(snapshot.five_hour_temporal_pct)
    data["five_hour_burn_rate"] = _round_rate(snapshot.five_hour_burn_rate)
    data["five_hour_started_at"] = _fmt_dt(snapshot.five_hour_started_at)
    data["seven_day_temporal_pct"] = _round_pct(snapshot.seven_day_temporal_pct)
    data["seven_day_burn_rate"] = _round_rate(snapshot.seven_day_burn_rate)
    data["seven_day_started_at"] = _fmt_dt(snapshot.seven_day_started_at)
    data["seven_day_opus_temporal_pct"] = _round_pct(snapshot.seven_day_opus_temporal_pct)
    data["seven_day_sonnet_temporal_pct"] = _round_pct(snapshot.seven_day_sonnet_temporal_pct)

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
