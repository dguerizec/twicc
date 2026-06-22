"""Atomic read/write of <data_dir>/seen-help.json.

Sibling of :mod:`twicc.seen_tips`. The on-disk format is the same flat
dict ``{<help_key>: <ISO timestamp UTC>}`` — one entry per help page the
user has dismissed with "Don't show this again" left on.
"""

from __future__ import annotations

import logging

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import get_seen_help_path

logger = logging.getLogger(__name__)


def read_seen_help() -> dict[str, str]:
    """Read the seen-help file.

    Returns an empty dict when the file is missing, invalid JSON, or not a
    dict at the top level. Non-string keys/values are dropped silently —
    this is a defensive read.
    """
    path = get_seen_help_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except orjson.JSONDecodeError:
        logger.warning("seen-help.json is invalid JSON, returning empty state")
        return {}
    if not isinstance(data, dict):
        logger.warning("seen-help.json is not a dict, returning empty state")
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def write_seen_help(state: dict[str, str]) -> None:
    """Persist the seen-help state atomically (whole-blob overwrite)."""
    atomic_write_json(get_seen_help_path(), state)
