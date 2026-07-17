"""Deterministic auto-generated project colors.

A project with no user-chosen color gets one derived from a stable hash of its
name (or, when unnamed, its final directory segment). Pure functions with no
Django dependency so they can be reused from the creation path
(:func:`twicc.projects.ensure_project_color`) and from the backfill data
migration alike.

The hue is the only hashed component; saturation and lightness are fixed at
values that stay legible on both light and dark surfaces. Tuning the constants
below only affects projects colored *after* the change — existing colors are
frozen in the DB and never recomputed.
"""

import hashlib
import os

# Fixed HSL saturation/lightness (percent). See the color-preview artifact used
# to pick these; the dot is a small filled circle shown on both light and dark
# backgrounds, so mid saturation + mid lightness reads on either.
PROJECT_COLOR_SATURATION = 65
PROJECT_COLOR_LIGHTNESS = 58


def _hsl_to_hex(hue: float, sat: float, light: float) -> str:
    """Convert an HSL triple (``hue`` in [0, 360), ``sat``/``light`` in
    [0, 100]) to a ``#rrggbb`` string. Mirrors the JS preview exactly."""
    sat /= 100
    light /= 100
    a = sat * min(light, 1 - light)

    def channel(n: int) -> int:
        k = (n + hue / 30) % 12
        c = light - a * max(-1, min(k - 3, 9 - k, 1))
        return round(255 * c)

    return f"#{channel(0):02x}{channel(8):02x}{channel(4):02x}"


def generate_project_color(label: str) -> str:
    """Return a deterministic ``#rrggbb`` color derived from *label*.

    Stable across processes and runs — uses SHA-256, never the per-process
    salted builtin ``hash`` — with strong avalanche, so near-identical labels
    (``api`` / ``apo``) land on clearly distinct hues.
    """
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:4], "big") % 360
    return _hsl_to_hex(hue, PROJECT_COLOR_SATURATION, PROJECT_COLOR_LIGHTNESS)


def _final_segment(directory: str | None) -> str:
    """Final path segment of *directory* (the folder name), or ``""``."""
    if not directory:
        return ""
    return os.path.basename(directory.rstrip("/"))


def color_for_project(name: str | None, directory: str | None) -> str | None:
    """Auto color for a project from its *name*, else its final directory segment.

    Returns ``None`` when neither yields a usable label, so the caller leaves the
    color unset. Callers must themselves skip git worktrees: those inherit their
    main repository's color rather than getting one of their own.
    """
    label = (name or "").strip() or _final_segment(directory)
    if not label:
        return None
    return generate_project_color(label)
