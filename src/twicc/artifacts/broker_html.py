"""Serve an HTML *artifact document* with the network broker wired in.

Two jobs, applied only to the **top-level** artifact HTML document (never its
sub-assets), per design 2026-06-18 §7 (CSP) + §8.3 (shim injection):

1. Inject the broker shim ``<script>`` as the first child of ``<head>`` so it
   runs before any artifact script and transparently routes ``fetch``/XHR
   through the host (the shim is DX; the CSP below is the real boundary).
2. Set the strict Content-Security-Policy **response header** — page-immutable,
   covers WebSockets — that makes ``connect-src`` the iframe's egress lock.
"""

from __future__ import annotations

import re

from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers

# Stable same-origin URL the injected tag points at; the built shim bundle is
# served here (route + bundle: phase 3c).
BROKER_SHIM_URL = "/_twicc/artifact-broker-shim.js"

# Egress lockdown (design §7). `connect-src 'none'` blocks every network call
# (fetch/XHR/WebSocket/EventSource/sendBeacon/<a ping>) — the iframe's only exit
# is postMessage to the host. `'unsafe-inline'` for script/style is intentional:
# the artifact *is* the untrusted script; CSP here controls egress, not the
# widget's own markup. Child contexts (about:blank/srcdoc/blob/data) inherit this,
# and `worker-src 'none'` forbids an un-patched worker network scope.
ARTIFACT_CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' blob:",
        "frame-src 'self'",
        "worker-src 'none'",
        "connect-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
)

_HEAD_RE = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_HTML_RE = re.compile(rb"<html[^>]*>", re.IGNORECASE)

# Request `Sec-Fetch-Dest` values that mean "this byte stream is loaded AS a
# page/frame" — the only ones we wrap (shim + CSP). Sub-resources (`script`,
# `style`, `image`, `font`, `empty` for fetch/XHR) keep streaming raw, so the
# artifact's own relative assets are never double-injected.
_DOCUMENT_FETCH_DESTS = frozenset({"iframe", "document"})


def is_artifact_document_request(sec_fetch_dest: str | None) -> bool:
    """Whether a raw-file request is loading the top-level artifact document
    (vs. a sub-resource), per the browser's ``Sec-Fetch-Dest`` header. Absent
    (non-browser client) → ``False``: serve raw, never guess."""
    return sec_fetch_dest in _DOCUMENT_FETCH_DESTS


def inject_broker_shim(html: bytes) -> bytes:
    """Insert the shim ``<script>`` as the first child of ``<head>``.

    Falls back to right after ``<html>`` (browsers synthesize a ``<head>``), or
    prepends to the document when neither tag is present. Byte-level so it never
    re-encodes the artifact's payload. A missed insertion is harmless — the CSP
    still blocks that artifact's direct egress (the shim is DX, not the boundary).
    """
    tag = f'<script src="{BROKER_SHIM_URL}"></script>'.encode()
    match = _HEAD_RE.search(html) or _HTML_RE.search(html)
    if match:
        return html[: match.end()] + tag + html[match.end() :]
    return tag + html


def artifact_html_response(html: bytes) -> HttpResponse:
    """Build the response for a top-level artifact HTML document: shim injected,
    strict CSP set, and the same nosniff + never-cache headers the raw byte path
    uses (the preview rewrites these files in place — a cached doc would run
    stale)."""
    response = HttpResponse(inject_broker_shim(html), content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = ARTIFACT_CSP
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    response["CDN-Cache-Control"] = "no-store"
    return response
