"""In-memory manifest of available help pages.

Sibling of :mod:`twicc.tips_manifest`. Built once at boot by scanning the
help assets dir and parsing each .md file's YAML front-matter. In dev
(``TWICC_DEBUG`` set by devctl), :func:`start_help_watcher_task` re-scans
periodically and re-broadcasts the manifest over ``help_manifest_pushed``
so adding/renaming/removing a help page no longer requires a restart.

The body of each .md is **not** read here. The frontend fetches it
directly via HTTP from /static/help/<key>.md.

Differences from tips: help pages are surfaced as a modal dialog driven by
explicit code events / buttons (not the random tip scheduler). Whether the
"don't show again" switch appears is decided per call site (a showHelp /
maybeAutoShow argument), not in the front-matter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import NamedTuple

import yaml

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[a-z0-9-]+$")
PLATFORM_VALUES = frozenset({"mobile", "desktop"})
OS_VALUES = frozenset({"mac", "linux", "windows"})
FRONT_MATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

# Dev-only polling interval (see twicc.tips_manifest for the rationale).
HELP_WATCH_INTERVAL_S = 10


class HelpMeta(NamedTuple):
    key: str
    title: str
    platform: list[str] | None
    os: list[str] | None
    providers_any: list[str] | None
    providers_all: list[str] | None


_manifest: dict[str, HelpMeta] = {}


def init_manifest(quiet: bool = False) -> None:
    """Scan the help dir and (re)bind the in-memory manifest.

    Called once at startup by :mod:`twicc.cli.run`, and repeatedly by
    :func:`start_help_watcher_task` in dev mode (``quiet=True``).
    """
    global _manifest
    from twicc.paths import get_help_assets_dir
    _manifest = scan_help_dir(get_help_assets_dir())
    if not quiet:
        logger.info("Help manifest: %d pages loaded", len(_manifest))


async def start_help_watcher_task(stop_event: asyncio.Event) -> None:
    """Dev-only periodic re-scan of the help dir.

    No-op unless ``TWICC_DEBUG`` is set (devctl injects it; production
    wheels never see it). Mirrors :func:`twicc.tips_manifest.start_tips_watcher_task`.
    """
    if os.environ.get("TWICC_DEBUG", "").strip().lower() not in ("1", "true", "yes"):
        return

    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()

    last_snapshot = manifest_to_dict()
    logger.info(
        "Help watcher: polling every %ds (TWICC_DEBUG enabled)",
        HELP_WATCH_INTERVAL_S,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HELP_WATCH_INTERVAL_S)
        except TimeoutError:
            # Timeout means it's time to re-scan
            pass
        else:
            # stop_event fired — exit cleanly
            break

        try:
            await asyncio.to_thread(init_manifest, True)
            current = manifest_to_dict()
            if current != last_snapshot:
                last_snapshot = current
                await channel_layer.group_send(
                    "updates",
                    {
                        "type": "broadcast",
                        "data": {
                            "type": "help_manifest_pushed",
                            "manifest": current,
                        },
                    },
                )
                logger.info(
                    "Help manifest changed, re-broadcasted (%d pages)",
                    len(current),
                )
        except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
            logger.exception("Help watcher: scan/broadcast failed")


def get_manifest() -> dict[str, HelpMeta]:
    return _manifest


def manifest_to_dict() -> dict[str, dict]:
    """JSON-serializable form for the WS / bootstrap wire payload."""
    return {
        key: {
            "title": page.title,
            "platform": page.platform,
            "os": page.os,
            "providers_any": page.providers_any,
            "providers_all": page.providers_all,
        }
        for key, page in _manifest.items()
    }


def scan_help_dir(directory: Path) -> dict[str, HelpMeta]:
    """Pure scan + parse + validate. Invalid pages are logged and excluded."""
    result: dict[str, HelpMeta] = {}
    if not directory.is_dir():
        logger.warning("Help directory not found: %s", directory)
        return result

    for path in sorted(directory.glob("*.md")):
        key = path.stem
        # Skip docs that obviously aren't help pages (README.md, …) by their
        # leading uppercase, keeping the boot log clean. The KEY_PATTERN
        # warning is reserved for files that look like pages but have a
        # malformed key (mixed case, underscore, accent).
        if key and key[0].isupper():
            continue
        if not KEY_PATTERN.match(key):
            logger.warning("Help %s: key does not match [a-z0-9-]+, skipped", key)
            continue
        try:
            meta = _parse_help_file(key, path)
        except (ValueError, OSError) as exc:
            logger.warning("Help %s: %s, skipped", key, exc)
            continue
        result[key] = meta
    return result


def _parse_help_file(key: str, path: Path) -> HelpMeta:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError("missing or malformed front-matter")
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front-matter: {exc}") from exc
    if not isinstance(fm, dict):
        # ValueError, not TypeError: the caller catches (ValueError, OSError) to
        # skip a single bad file and keep scanning.
        raise ValueError("front-matter must be a YAML mapping")  # noqa: TRY004

    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing or invalid 'title'")

    return HelpMeta(
        key=key,
        title=title.strip(),
        platform=_validate_array(fm, "platform", PLATFORM_VALUES),
        os=_validate_array(fm, "os", OS_VALUES),
        providers_any=_validate_array(fm, "providers_any", allowed=None),
        providers_all=_validate_array(fm, "providers_all", allowed=None),
    )


def _validate_array(
    fm: dict, field: str, allowed: frozenset[str] | None
) -> list[str] | None:
    """Validate the optional ``field``. Returns None if absent, otherwise a list."""
    if field not in fm:
        return None
    value = fm[field]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"'{field}' must be an array of strings")
    if allowed is not None:
        bad = [x for x in value if x not in allowed]
        if bad:
            raise ValueError(f"'{field}' has invalid values: {bad}")
    return value
