"""Build fail-closed Share URLs from backend-normalized public origins.

The Python regression fixture covers legacy repair and backend URL construction.
Frontend stored consumers use their own narrower canonical-shape tests.
"""

from __future__ import annotations

def normalize_share_base(value: str | None) -> str:
    """Return a canonical public origin, or empty for invalid storage."""
    from twicc.core.services.public_origin import usable_public_origin

    return usable_public_origin(value)


def build_share_url(base_value: str | None, url_path: str) -> str | None:
    """Return an absolute Share URL, or ``None`` for an unusable base."""
    base = normalize_share_base(base_value)
    return base + url_path if base else None
