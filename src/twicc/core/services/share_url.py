"""Share URL building — the parity contract of the agent-sharing design §7.4.

Mirrored with ``buildShareUrl`` in ``frontend/src/utils/shareUrlCore.js``: SAME
algorithm, byte-identical output for the same stored ``shareBaseUrl``. The
normative trim set below is part of the contract — do NOT switch to
``str.strip()`` (its Unicode set differs from JS ``trim()``). Parity is
enforced by the shared fixture ``tests/fixtures/share_url_parity.json``.

No validity guarantee: a bare ``host`` / ``host:port`` becomes an absolute
HTTPS URL; any other non-empty stored value passes through the algorithm
unchanged (a pre-existing configuration defect stays visible identically on
both surfaces).
"""

from __future__ import annotations

# Normative trim set (§7.4): ASCII whitespace only — TAB LF VT FF CR SPACE.
_TRIM_CHARS = "\t\n\x0b\x0c\r "


def normalize_share_base(value: str | None) -> str:
    """Trim the normative set, then strip trailing slashes. Empty stays empty."""
    return (value or "").strip(_TRIM_CHARS).rstrip("/")


def build_share_url(base_value: str | None, url_path: str) -> str:
    """Absolute share URL for a NON-EMPTY stored ``shareBaseUrl``.

    Callers handle the empty base themselves (CLI: relative path; frontend:
    ``null`` — the deliberate unset-host split, §7.4)."""
    base = normalize_share_base(base_value)
    if "://" not in base:
        base = "https://" + base
    return base + url_path
