"""Response hardening applied to every ``/share/`` response (design §6.1)."""

from __future__ import annotations


def apply_share_headers(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response
