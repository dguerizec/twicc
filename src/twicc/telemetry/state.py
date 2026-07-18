"""Telemetry state file (<data_dir>/telemetry.json).

Holds the anonymous instance id, the last-sent marker (the no-backfill
rule: initialized to *today* on first run so pre-telemetry DB history is
never sent — design doc §5.1), the per-day accumulators for the two
metrics that have no DB trace (presence minutes, peak concurrent agents),
a copy of the last payload sent (for the settings "View last payload"
dialog), and ``last_sent_at`` (UTC timestamp of that last successful send).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from twicc.atomic_json import locked_json_file
from twicc.paths import get_data_dir

STATE_FILENAME = "telemetry.json"

# Offline catch-up cap (design §5.1): older unsent days are dropped.
MAX_DAY_ENTRIES = 30

_DEFAULT_STATE = {
    "instance_id": None,
    "last_sent_date": None,
    "days": {},
    "last_payload": None,
    "last_sent_at": None,
}


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def get_state_path() -> Path:
    return get_data_dir() / STATE_FILENAME


@contextmanager
def state_txn():
    """Locked read-modify-write on the state file, defaults ensured."""
    with locked_json_file(get_state_path(), default=dict(_DEFAULT_STATE)) as txn:
        for key, value in _DEFAULT_STATE.items():
            txn.data.setdefault(key, value if not isinstance(value, dict) else dict(value))
        if not txn.data["instance_id"]:
            txn.data["instance_id"] = str(uuid.uuid4())
            txn.write()
        if not txn.data["last_sent_date"]:
            txn.data["last_sent_date"] = utc_today().isoformat()
            txn.write()
        yield txn


def ensure_state() -> dict:
    with state_txn() as txn:
        return dict(txn.data)


def reset_instance_id() -> str:
    with state_txn() as txn:
        txn.data["instance_id"] = str(uuid.uuid4())
        txn.write()
        return txn.data["instance_id"]


def record_tick(*, present: bool, live_agents: int) -> None:
    """One ticker sample: +1 presence minute if present, max() the peak."""
    day = utc_today().isoformat()
    with state_txn() as txn:
        entry = txn.data["days"].setdefault(day, {"presence_minutes": 0, "peak_agents": 0})
        if present:
            entry["presence_minutes"] += 1
        entry["peak_agents"] = max(entry["peak_agents"], live_agents)
        _prune(txn.data)
        txn.write()


def mark_sent(sent_through: str, payload: dict) -> None:
    """Advance the marker after a successful POST and drop covered days."""
    with state_txn() as txn:
        txn.data["last_sent_date"] = sent_through
        txn.data["last_payload"] = payload
        txn.data["last_sent_at"] = datetime.now(timezone.utc).isoformat()
        txn.data["days"] = {d: v for d, v in txn.data["days"].items() if d > sent_through}
        txn.write()


def _prune(data: dict) -> None:
    days = data["days"]
    if len(days) > MAX_DAY_ENTRIES:
        for day in sorted(days)[: len(days) - MAX_DAY_ENTRIES]:
            del days[day]
