"""Atomic read/write of <data_dir>/seen-tips.json.

Mirrors src/twicc/agent_settings_presets.py for the file pattern, simplified
because we don't have a provider dimension : there's exactly one file.

The on-disk format is a flat dict ``{<tip_key>: <ISO timestamp UTC>}``.
"""

from __future__ import annotations

import logging
import os
import tempfile

import orjson

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
    """Persist the seen-tips state atomically (tempfile + os.replace)."""
    path = get_seen_tips_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(state, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
