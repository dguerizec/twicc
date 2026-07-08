"""Server-rendered HTML shells for the share pages (data-island pattern, like
``artifacts/broker_html.py``)."""

from __future__ import annotations

from html import escape

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
        f"<title>{escape(title)}</title>\n"
        f'<link rel="stylesheet" href="{SHARE_SESSION_CSS_URL}">\n'
        f'<script type="application/json" id="{_DATA_ID}">{payload}</script>\n'
        f'<script type="module" src="{SHARE_SESSION_JS_URL}"></script>\n'
        "</head>\n<body class=\"loading\">\n<div id=\"app\"></div>\n</body>\n</html>\n"
    ).encode()


def _document_title(label: str, shared: str = "") -> str:
    """Browser tab title: ``TwiCC - <label> - <shared item title>`` (the item title
    is omitted when it's hidden or empty)."""
    shared = (shared or "").strip()
    return f"TwiCC - {label} - {shared}" if shared else f"TwiCC - {label}"


def share_page_response(*, token_path: str, meta: dict, mode: str = "session") -> HttpResponse:
    """The shared-session viewer page (``mode="session"``) or the markdown/mermaid
    doc view (``mode="doc"``). ``meta`` is the public meta island."""
    data = {"tokenPath": token_path, "mode": mode, "meta": meta}
    kind = meta.get("kind") if isinstance(meta, dict) else None
    shared = meta.get("title") if isinstance(meta, dict) else None
    label = "Shared artifact" if kind == "artifact" else "Shared session"
    resp = HttpResponse(_page(data, title=_document_title(label, shared or "")),
                        content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_recent_response() -> HttpResponse:
    """The share host homepage (``/share/`` — no token): a client-side list of the
    shares this browser has opened, read from ``localStorage`` on the share origin
    (design §12). Carries NO server data — no per-viewer tracking exists."""
    resp = HttpResponse(_page({"mode": "recent"}, title=_document_title("Shared with you")),
                        content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


# Standalone, JS-free page for the uniform-404 outcome. Generic by design: the
# uniform-404 policy (design §6.1/§14 — "the same minimal 404 page … no oracle")
# forbids leaking which case it is (unknown / revoked / expired / deleted) or even
# the share's kind, so the copy never says "session" / "artifact". Inline styles
# keep it self-contained (no bundle dependency) and dark-mode aware.
_UNAVAILABLE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>TwiCC - Share unavailable</title>
<style>
:root { color-scheme: light dark;
  --bg:#f4f5f7; --card:#fff; --fg:#1f2328; --muted:#6b7280; --border:#e3e6ea; --accent:#0891b2; }
@media (prefers-color-scheme: dark) { :root {
  --bg:#16181d; --card:#1e2127; --fg:#e6e8eb; --muted:#9aa3ad; --border:#2b2f36; --accent:#22d3ee; } }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 100%; padding: 1.5rem; background: var(--bg); color: var(--fg);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.5; }
.card { max-width: 30rem; width: 100%; text-align: center; background: var(--card);
  border: 1px solid var(--border); border-radius: 14px; padding: 2.5rem 2rem;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.icon { width: 2.75rem; height: 2.75rem; color: var(--muted); margin-bottom: 1rem; }
h1 { font-size: 1.35rem; margin: 0 0 .5rem; font-weight: 650; }
p { margin: 0 auto; color: var(--muted); max-width: 24rem; }
.home { display: inline-block; margin-top: 1.75rem; color: var(--accent);
  text-decoration: none; font-weight: 550; }
.home:hover { text-decoration: underline; }
footer { margin-top: 2rem; color: var(--muted); font-size: .8rem; }
</style>
</head>
<body>
<div class="card">
<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
<path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 0 1 4 8"/><line x1="3" y1="3" x2="21" y2="21"/>
</svg>
<h1>This share is no longer available</h1>
<p>The link may have expired or is no longer shared.</p>
<a class="home" href="/share/">See your shared links</a>
</div>
<footer>Shared with TwiCC</footer>
<script>
// Self-heal this browser's recent-shares list: a link that lands here is gone, so
// drop it (mirrors removeRecentShare — the share bundle isn't loaded on this page).
// KEY must match frontend/src/share-recent/recordView.js. Same-origin localStorage
// only; nothing is sent anywhere.
(function () {
  try {
    var m = location.pathname.match(/^\\/share\\/([^\\/]+)/);
    if (!m) return;
    var KEY = 'twicc-share-recent';
    var arr = JSON.parse(localStorage.getItem(KEY) || '[]');
    if (!Array.isArray(arr)) return;
    var next = arr.filter(function (e) { return e && e.token !== m[1]; });
    if (next.length !== arr.length) localStorage.setItem(KEY, JSON.stringify(next));
  } catch (e) { /* opaque origin / storage disabled */ }
})();
</script>
</body>
</html>
""".encode()


def share_unavailable_response() -> HttpResponse:
    """Friendly 404 page for an unavailable share (design's "minimal 404 page"),
    replacing Django's bare error. Status stays 404; the body is a real page."""
    resp = HttpResponse(_UNAVAILABLE_PAGE, status=404, content_type="text/html; charset=utf-8")
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
        f"<title>{escape(_document_title('Shared artifact', title))}</title>\n"
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
