"""Server-rendered HTML shells for the share pages (data-island pattern, like
``artifacts/broker_html.py``)."""

from __future__ import annotations

import orjson
from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers

from twicc.artifacts.broker_html import ARTIFACT_CSP, inject_broker_shim
from twicc.share.headers import apply_share_headers

SHARE_SESSION_JS_URL = "/_twicc/share/share-session.js"
SHARE_SESSION_CSS_URL = "/_twicc/share/share-session.css"
_DATA_ID = "twicc-share-data"


def _page(data: dict, *, title: str) -> bytes:
    payload = orjson.dumps(data).decode().replace("<", "\\u003c")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{title}</title>\n"
        f'<link rel="stylesheet" href="{SHARE_SESSION_CSS_URL}">\n'
        f'<script type="application/json" id="{_DATA_ID}">{payload}</script>\n'
        f'<script type="module" src="{SHARE_SESSION_JS_URL}"></script>\n'
        "</head>\n<body class=\"loading\">\n<div id=\"app\"></div>\n</body>\n</html>\n"
    ).encode()


def share_page_response(*, token_path: str, meta: dict, mode: str = "session") -> HttpResponse:
    """The shared-session viewer page (``mode="session"``) or the markdown/mermaid
    doc view (``mode="doc"``). ``meta`` is the public meta island."""
    data = {"tokenPath": token_path, "mode": mode, "meta": meta}
    resp = HttpResponse(_page(data, title="Shared session"), content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_recent_response() -> HttpResponse:
    """The share host homepage (``/share/`` — no token): a client-side list of the
    shares this browser has opened, read from ``localStorage`` on the share origin
    (design §12). Carries NO server data — no per-viewer tracking exists."""
    resp = HttpResponse(_page({"mode": "recent"}, title="Shared with you"),
                        content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_artifact_shell_response(*, token_path: str, inner_doc_url: str, snapshot_at, title: str = "") -> HttpResponse:
    """Trusted shell for an HTML artifact share: iframes the CSP-wrapped inner doc,
    mounts the prompt-less broker (design §9.3). Reuses the artifact-shell bundle
    in share mode via its data island. ``title`` only seeds the recent-shares list."""
    data = {"mode": "share", "tokenPath": token_path, "innerDocUrl": inner_doc_url,
            "snapshotAt": snapshot_at, "title": title}
    payload = orjson.dumps(data).decode().replace("<", "\\u003c")
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>Shared artifact</title>\n"
        '<link rel="stylesheet" href="/_twicc/artifact-shell/shell.css">\n'
        f'<script type="application/json" id="twicc-shell-data">{payload}</script>\n'
        '<script type="module" src="/_twicc/artifact-shell/shell.js"></script>\n'
        "</head>\n<body>\n<div id=\"app\"></div>\n</body>\n</html>\n"
    ).encode()
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_artifact_doc_response(html: bytes) -> HttpResponse:
    """The artifact's inner document: shim injected + strict CSP, share-hardened."""
    resp = HttpResponse(inject_broker_shim(html), content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = ARTIFACT_CSP
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)
