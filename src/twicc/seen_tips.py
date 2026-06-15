"""Atomic read/write of <data_dir>/seen-tips.json.

Mirrors src/twicc/agent_settings_presets.py for the file pattern, simplified
because we don't have a provider dimension : there's exactly one file.

The on-disk format is a flat dict ``{<tip_key>: <ISO timestamp UTC>}``.
"""

from __future__ import annotations

import logging

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import get_seen_tips_path

logger = logging.getLogger(__name__)


def read_seen_tips() -> dict[str, str]:
    """Read the seen-tips file.

    Returns an empty dict when the file is missing, invalid JSON, or not a
    dict at the top level. Keys and values that are not strings are dropped
    silently — this is a defensive read.
    """
    path = get_seen_tips_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except orjson.JSONDecodeError:
        logger.warning("seen-tips.json is invalid JSON, returning empty state")
        return {}
    if not isinstance(data, dict):
        logger.warning("seen-tips.json is not a dict, returning empty state")
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def write_seen_tips(state: dict[str, str]) -> None:
    """Persist the seen-tips state atomically (whole-blob overwrite)."""
    atomic_write_json(get_seen_tips_path(), state)
