"""Authentication views for password-based login.

Provides login, logout, and auth status check endpoints.
All endpoints are under /api/auth/ and always accessible (no auth required).

Password storage and verification (PBKDF2-SHA256, with legacy SHA-256
support for hashes set before the upgrade) lives in ``twicc.auth.hashers``.
"""

import html
import logging
import time
from collections import defaultdict
from urllib.parse import parse_qs

import asyncio

import orjson
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse

from twicc.auth.hashers import verify_password
from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    SESSION_FINGERPRINT_KEY,
    bind_session,
    is_session_authenticated,
)

logger = logging.getLogger(__name__)


# ── Rate limiter for login brute-force protection ─────────────────────────

# Per-IP tracking of failed login attempts
_login_attempts: dict[str, list[float]] = defaultdict(list)

# Max failed attempts before throttling
_MAX_ATTEMPTS = 5
# Time window in seconds (failed attempts older than this are forgotten)
_WINDOW_SECONDS = 300  # 5 minutes
# Lockout duration after exceeding max attempts
_LOCKOUT_SECONDS = 60


def _check_rate_limit(ip: str) -> int | None:
    """Check if an IP is rate-limited.

    Returns None if allowed, or the number of seconds to wait if blocked.
    """
    now = time.monotonic()
    attempts = _login_attempts[ip]

    # Prune old attempts outside the window
    _login_attempts[ip] = [t for t in attempts if now - t < _WINDOW_SECONDS]
    attempts = _login_attempts[ip]

    if len(attempts) >= _MAX_ATTEMPTS:
        last_attempt = attempts[-1]
        wait = int(_LOCKOUT_SECONDS - (now - last_attempt))
        if wait > 0:
            return wait
        # Lockout expired — clear and allow
        _login_attempts[ip] = []

    return None


def _record_failed_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _login_attempts[ip].append(time.monotonic())


def _get_client_ip(request) -> str:
    """Extract the real client IP from the request.

    Checks proxy/tunnel headers in priority order before falling back to REMOTE_ADDR.
    Only the leftmost (client) IP is used from X-Forwarded-For to avoid spoofing
    via appended values.

    Header priority:
        1. CF-Connecting-IP  (Cloudflare Tunnel)
        2. Fly-Client-IP     (Fly.io)
        3. X-Real-IP         (Nginx convention)
        4. X-Forwarded-For   (standard proxy header, first IP only)
        5. REMOTE_ADDR       (direct connection fallback)
    """
    # Tunnel/proxy-specific headers (single IP, most trustworthy when present)
    for header in ("HTTP_CF_CONNECTING_IP", "HTTP_FLY_CLIENT_IP", "HTTP_X_REAL_IP"):
        ip = request.META.get(header)
        if ip:
            return ip.strip()

    # Standard proxy header — take the first (leftmost) IP only
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "unknown")


async def auth_check(request):
    """GET /api/auth/check/ - Check if user is authenticated.

    Returns:
        - {"authenticated": true, "password_required": true} if authenticated
        - {"authenticated": true, "password_required": false} if no password configured
        - {"authenticated": false, "password_required": true} if not authenticated
    """
    stored_hash = settings.TWICC_PASSWORD_HASH
    if not stored_hash:
        return JsonResponse({"authenticated": True, "password_required": False})

    session = request.session
    auth_value = await session.aget(SESSION_AUTH_KEY)
    fingerprint = await session.aget(SESSION_FINGERPRINT_KEY)
    authenticated = is_session_authenticated(
        auth_value,
        fingerprint,
        stored_hash,
    )
    # Drop a stale session so the next request doesn't keep retrying it.
    if not authenticated and auth_value:
        await session.aflush()

    return JsonResponse({
        "authenticated": authenticated,
        "password_required": True,
    })


async def login(request):
    """POST /api/auth/login/ - Authenticate with password.

    Body: {"password": "the_password"}

    Verification (constant-time, dispatched per format) is delegated to
    ``verify_password`` so both legacy SHA-256 and current PBKDF2 hashes
    are accepted.

    On success, sets session["authenticated"] = True and returns 200.
    On failure, returns 401.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not settings.TWICC_PASSWORD_HASH:
        return JsonResponse({"error": "No password configured"}, status=400)

    ip = _get_client_ip(request)

    # Rate limit check
    wait = _check_rate_limit(ip)
    if wait is not None:
        logger.warning("Login rate-limited for %s (%ds remaining)", ip, wait)
        return JsonResponse(
            {"error": f"Too many attempts. Try again in {wait}s."},
            status=429,
        )

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    password = data.get("password", "")

    # PBKDF2 verification is intentionally CPU-heavy — running it on the
    # event loop would stall unrelated async work for the entire request.
    # ``asyncio.to_thread`` ships it to a fresh worker thread; using
    # ``sync_to_async(...)`` here would default to ``thread_sensitive=True``
    # which routes through asgiref's single shared executor, serialising
    # all PBKDF2 verifications back-to-back.
    if await asyncio.to_thread(verify_password, password, settings.TWICC_PASSWORD_HASH):
        # ``bind_session`` mutates the session dict (``session[key] = ...``)
        # synchronously. Touching the session for the first time would
        # otherwise trigger a sync ORM load — async-pre-load it via
        # ``aget`` so the dict writes that follow are pure in-memory.
        # SessionMiddleware persists the modified session at response time
        # (SESSION_SAVE_EVERY_REQUEST=True).
        await request.session.aget(SESSION_AUTH_KEY)
        bind_session(request.session, settings.TWICC_PASSWORD_HASH)
        # Clear failed attempts on success
        _login_attempts.pop(ip, None)
        logger.info("Successful login from %s", ip)
        return JsonResponse({"authenticated": True})
    else:
        _record_failed_attempt(ip)
        remaining = _MAX_ATTEMPTS - len(_login_attempts[ip])
        logger.warning("Failed login attempt from %s (%d attempts left)", ip, max(0, remaining))
        return JsonResponse({"error": "Invalid password"}, status=401)


async def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    await request.session.aflush()
    return JsonResponse({"authenticated": False})


# ── Standalone artifact password page ─────────────────────────────────────
# A direct artifact URL (/artifacts/<id>/…) opened in a fresh tab may hit an
# unauthenticated session; PasswordAuthMiddleware redirects it here. This is
# plain server-rendered web on purpose: no SPA, and no JS/API for the auth
# itself — just a form POST. The only script is a tiny inline scheme-picker.

# Kept as a bare string (CSS/JS braces, no formatting applied) so the inline
# theme script can sit as high as possible in <head> and flip the colour scheme
# before first paint. Dark is the default; the page has only a handful of
# elements, so the CSS stays deliberately small.
_AUTH_PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TwiCC — Password required</title>
<script>
/* Pick the saved colour scheme before paint to avoid a flash. Dark is the
   default; switch to light only when the stored setting (or, when "system",
   the OS preference) asks for it. */
(function(){try{
  var s=JSON.parse(localStorage.getItem('twicc-settings')||'{}');
  var cs=s.colorScheme||'system';
  var dark=cs==='dark'||(cs!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches);
  if(!dark)document.documentElement.classList.add('light');
}catch(e){}})();
</script>
<style>
:root{--bg:#18181b;--card:#27272a;--border:#3f3f46;--text:#f4f4f5;--muted:#a1a1aa;--brand:#06b6d4;--brand-text:#08171c;--input-bg:#18181b;--danger-bg:#7f1d1d;--danger-text:#fecaca;color-scheme:dark}
:root.light{--bg:#f4f4f5;--card:#ffffff;--border:#e4e4e7;--text:#18181b;--muted:#71717a;--brand:#0891b2;--brand-text:#ffffff;--input-bg:#ffffff;--danger-bg:#fee2e2;--danger-text:#991b1b;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
.card{width:100%;max-width:360px;margin:1rem;padding:2rem;background:var(--card);border:1px solid var(--border);border-radius:12px}
.head{text-align:center;margin-bottom:1.5rem}
.title{margin:0;font-size:1.4rem;font-weight:700;letter-spacing:.05em}
.sub{margin:.4rem 0 0;font-size:.85rem;color:var(--muted)}
label{display:block;font-size:.85rem;font-weight:600;margin-bottom:.4rem}
input[type=password]{width:100%;padding:.6rem .7rem;font-size:1rem;border-radius:8px;border:1px solid var(--border);background:var(--input-bg);color:var(--text)}
input[type=password]:focus{outline:2px solid var(--brand);outline-offset:1px}
.err{margin:.9rem 0 0;padding:.55rem .7rem;border-radius:8px;font-size:.85rem;background:var(--danger-bg);color:var(--danger-text)}
button{width:100%;margin-top:1.1rem;padding:.65rem;font-size:.95rem;font-weight:600;border:none;border-radius:8px;background:var(--brand);color:var(--brand-text);cursor:pointer}
button:hover{filter:brightness(1.08)}
</style>
</head>
<body>
"""

_AUTH_PAGE_TAIL = "</body></html>"


def _safe_artifact_redirect(value: str) -> str:
    """Confine the post-login redirect to a local artifact path — no open-redirect,
    no loop back to the auth page."""
    if (
        value
        and value.startswith("/artifacts/")
        and not value.startswith("//")
        and not value.startswith("/artifacts/auth")
    ):
        return value
    return "/"


def _artifact_auth_page(redirect_target: str, *, error: bool) -> str:
    err = '<p class="err">Incorrect password. Try again.</p>' if error else ""
    redirect_attr = html.escape(redirect_target, quote=True)
    return (
        _AUTH_PAGE_HEAD
        + '<form class="card" method="post" action="/artifacts/auth">'
        + '<div class="head"><h1 class="title">TwiCC</h1>'
        + '<p class="sub">Password required to view this artifact</p></div>'
        + f'<input type="hidden" name="redirect" value="{redirect_attr}">'
        + '<label for="pw">Password</label>'
        + '<input id="pw" type="password" name="password" placeholder="Enter password"'
        + ' autofocus autocomplete="current-password">'
        + err
        + '<button type="submit">Sign in</button>'
        + '</form>'
        + _AUTH_PAGE_TAIL
    )


async def artifact_auth(request):
    """GET/POST /artifacts/auth — standalone password page for direct artifact access.

    PasswordAuthMiddleware redirects unauthenticated ``/artifacts/*`` navigations
    here with ``?redirect=<target>``. On success it binds the session (sets the
    cookie) and 302s back to the target. Shares the login brute-force rate limiter.
    """
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)

    if request.method == "GET":
        target = _safe_artifact_redirect(request.GET.get("redirect", ""))
        # No password configured → nothing to gate; go straight through.
        if not settings.TWICC_PASSWORD_HASH:
            return HttpResponseRedirect(target)
        return HttpResponse(_artifact_auth_page(target, error=False), content_type="text/html")

    # POST — validate and (on success) authenticate.
    body = parse_qs(request.body.decode("utf-8", "replace"))
    target = _safe_artifact_redirect((body.get("redirect") or [""])[0])
    if not settings.TWICC_PASSWORD_HASH:
        return HttpResponseRedirect(target)

    ip = _get_client_ip(request)
    wait = _check_rate_limit(ip)
    if wait is not None:
        logger.warning("Artifact-auth rate-limited for %s (%ds remaining)", ip, wait)
        return HttpResponse(
            _artifact_auth_page(target, error=True), content_type="text/html", status=429,
        )

    password = (body.get("password") or [""])[0]
    if await asyncio.to_thread(verify_password, password, settings.TWICC_PASSWORD_HASH):
        await request.session.aget(SESSION_AUTH_KEY)
        bind_session(request.session, settings.TWICC_PASSWORD_HASH)
        _login_attempts.pop(ip, None)
        logger.info("Successful artifact-auth login from %s", ip)
        return HttpResponseRedirect(target)

    _record_failed_attempt(ip)
    return HttpResponse(
        _artifact_auth_page(target, error=True), content_type="text/html", status=401,
    )
