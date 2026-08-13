"""ASGI gate enforcing the mandatory dedicated share origin (design §12).

Sharing is served ONLY on the configured share host — the hostname of the
``shareBaseUrl`` synced setting — and NEVER on the working origin. The gate reads
that setting LIVE per request (the way ``external_notifications.py`` reads
``publicBaseUrl``), so an Apply in Settings → Sharing takes effect with no restart:

  request Host == share host   → ShareOnlyApp: only /share/… (+ public share assets),
                                  everything else 404; WS only for ws/share/… .
  request Host != share host   → the full app, but /share/… + /_twicc/share/… → 404,
                                  and ws/share/… closed.
  share host unset (empty)     → /share/… 404s everywhere (sharing disabled).

Rationale: the public share surface must never share an origin with the
authenticated working app. Cookie isolation needs a distinct *hostname* (cookies
are not port-scoped), so the share host is a different hostname pointing at the
same local port. ``/mcp`` (pre-Django ``http_router``) is absent from the
allow-list below, so it is never reachable on the share host.
"""

from __future__ import annotations

# Served on the share host. NOTE: /_twicc/artifact-shell/ and the broker shim are
# shared with the working app's own artifact preview, so they are allowed on the
# share host but must NOT be 404'd on the working origin — only /share/ and
# /_twicc/share/ are share-exclusive.
_SHARE_ONLY_PREFIXES = (
    "/share/",
    "/_twicc/share/",
    "/_twicc/artifact-shell/",
    "/_twicc/artifact-broker-shim.js",
)
# Share-exclusive: 404'd on any non-share origin.
_SHARE_EXCLUSIVE_PREFIXES = ("/share/", "/_twicc/share/")


def _share_only_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in _SHARE_ONLY_PREFIXES) or path == "/favicon.ico"


def _live_share_host() -> str:
    """Current share hostname from the synced settings (lower-case, no scheme/port)."""
    from twicc.core.services.public_origin import normalize_public_origin
    from twicc.synced_settings import read_synced_settings  # cached in-memory dict
    result = normalize_public_origin(read_synced_settings().get("shareBaseUrl"))
    return result.hostname or ""


def _request_host(scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"host":
            return value.decode("latin1").split(":", 1)[0].lower()
    return ""


async def _reply_404(send):
    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Not found"})


async def _reply_204(send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _reply_redirect(send, location: str):
    # 302 Found — TEMPORARY on purpose: the share-host root points at /share/ for now,
    # but a real homepage could live there later, so it must not be cached permanently.
    await send({"type": "http.response.start", "status": 302,
                "headers": [(b"location", location.encode("latin1")),
                            (b"cache-control", b"no-store"),
                            (b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b""})


class ShareOnlyApp:
    """Wrap an ASGI app, exposing ONLY the share surface (used on the share host)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        path = scope.get("path", "")
        if stype == "http":
            if path == "/":
                # Share-host root → the recent-shares homepage (temporary redirect).
                return await _reply_redirect(send, "/share/")
            if path == "/favicon.ico":
                return await _reply_204(send)
            if not _share_only_allowed(path):
                return await _reply_404(send)
            return await self.inner(scope, receive, send)
        if stype == "websocket":
            if not path.startswith("/ws/share/"):
                await send({"type": "websocket.close", "code": 4404})
                return
            return await self.inner(scope, receive, send)
        # lifespan et al. pass through.
        return await self.inner(scope, receive, send)


class ShareHostGate:
    """Route by the live ``shareBaseUrl`` host. On the share host, serve share-only;
    on every other origin, serve the full app but hide the share surface (404)."""

    def __init__(self, full_app, share_only_app):
        self.full_app = full_app
        self.share_only_app = share_only_app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket"):
            return await self.full_app(scope, receive, send)
        share_host = _live_share_host()
        if share_host and _request_host(scope) == share_host:
            return await self.share_only_app(scope, receive, send)
        # Non-share origin: full app, but the share surface is invisible here.
        path = scope.get("path", "")
        if stype == "http" and any(path.startswith(p) for p in _SHARE_EXCLUSIVE_PREFIXES):
            return await _reply_404(send)
        if stype == "websocket" and path.startswith("/ws/share/"):
            await send({"type": "websocket.close", "code": 4404})
            return
        return await self.full_app(scope, receive, send)
