"""In-memory manifest of available tips.

Built once at boot by scanning the tips assets dir and parsing each .md
file's YAML front-matter. Read-only after init — adding / removing tip
files requires a restart of the backend.

The body of each .md is **not** read here. The frontend fetches it
directly via HTTP from /static/tips/<key>.md.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

import yaml

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[a-z0-9-]+$")
PLATFORM_VALUES = frozenset({"mobile", "desktop"})
OS_VALUES = frozenset({"mac", "linux", "windows"})
FRONT_MATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class TipMeta(NamedTuple):
    key: str
    title: str
    platform: list[str] | None
    os: list[str] | None
    providers_any: list[str] | None
    providers_all: list[str] | None


_manifest: dict[str, TipMeta] = {}


def init_manifest() -> None:
    """Called once at startup. Scans the tips dir and fills the in-memory manifest."""
    global _manifest
    from twicc.paths import get_tips_assets_dir
    _manifest = scan_tips_dir(get_tips_assets_dir())
    logger.info("Tips manifest: %d tips loaded", len(_manifest))


def get_manifest() -> dict[str, TipMeta]:
    return _manifest


def manifest_to_dict() -> dict[str, dict]:
    """JSON-serializable form for the WS / bootstrap wire payload."""
    return {
        key: {
            "title": tip.title,
            "platform": tip.platform,
            "os": tip.os,
            "providers_any": tip.providers_any,
            "providers_all": tip.providers_all,
        }
        for key, tip in _manifest.items()
    }


def scan_tips_dir(directory: Path) -> dict[str, TipMeta]:
    """Pure scan + parse + validate. Invalid tips are logged and excluded."""
    result: dict[str, TipMeta] = {}
    if not directory.is_dir():
        logger.warning("Tips directory not found: %s", directory)
        return result

    for path in sorted(directory.glob("*.md")):
        key = path.stem
        # Silently skip files that obviously aren't tips: anything whose
        # name starts with an uppercase letter (README.md, LICENSE.md, …).
        # The KEY_PATTERN check below would already exclude them, but we
        # treat them as documentation files so the boot log stays clean.
        # The warning is reserved for files that look like tips but have
        # malformed keys (e.g. mixed case, underscore, accent).
        if key and key[0].isupper():
            continue
        if not KEY_PATTERN.match(key):
            logger.warning("Tip %s: key does not match [a-z0-9-]+, skipped", key)
            continue
        try:
            meta = _parse_tip_file(key, path)
        except (ValueError, OSError) as exc:
            # ValueError covers parse / validation failures (incl. UnicodeDecodeError).
            # OSError covers permission denied, IsADirectoryError, etc. — the spec
            # requires the scan to keep going through any single bad file.
            logger.warning("Tip %s: %s, skipped", key, exc)
            continue
        result[key] = meta
    return result


def _parse_tip_file(key: str, path: Path) -> TipMeta:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError("missing or malformed front-matter")
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front-matter: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError("front-matter must be a YAML mapping")

    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing or invalid 'title'")

    return TipMeta(
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
