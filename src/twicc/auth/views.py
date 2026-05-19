"""Authentication views for password-based login.

Provides login, logout, and auth status check endpoints.
All endpoints are under /api/auth/ and always accessible (no auth required).

Password storage and verification (PBKDF2-SHA256, with legacy SHA-256
support for hashes set before the upgrade) lives in ``twicc.auth.hashers``.
"""

import logging
import time
from collections import defaultdict

import orjson
from django.conf import settings
from django.http import JsonResponse

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


def auth_check(request):
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
    authenticated = is_session_authenticated(
        session.get(SESSION_AUTH_KEY),
        session.get(SESSION_FINGERPRINT_KEY),
        stored_hash,
    )
    # Drop a stale session so the next request doesn't keep retrying it.
    if not authenticated and session.get(SESSION_AUTH_KEY):
        session.flush()

    return JsonResponse({
        "authenticated": authenticated,
        "password_required": True,
    })


def login(request):
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

    if verify_password(password, settings.TWICC_PASSWORD_HASH):
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


def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    request.session.flush()
    return JsonResponse({"authenticated": False})
