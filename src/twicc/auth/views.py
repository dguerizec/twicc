"""Authentication views for password-based login and API token management.

Provides login, logout, auth status check, and API token endpoints.
All endpoints are under /api/auth/ and always accessible (no middleware auth).

The password is never stored in clear text. TWICC_PASSWORD_HASH contains a
SHA-256 hex digest. On login, the submitted password is hashed and compared
to the stored hash using constant-time comparison.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets

import orjson
from django.conf import settings
from django.http import JsonResponse

from twicc.paths import get_env_path

logger = logging.getLogger(__name__)


def _hash_password(password: str) -> str:
    """Hash a password with SHA-256 and return its hex digest."""
    return hashlib.sha256(password.encode()).hexdigest()


def auth_check(request):
    """GET /api/auth/check/ - Check if user is authenticated.

    Returns:
        - {"authenticated": true, "password_required": true} if authenticated
        - {"authenticated": true, "password_required": false} if no password configured
        - {"authenticated": false, "password_required": true} if not authenticated
    """
    password_required = bool(settings.TWICC_PASSWORD_HASH)
    has_api_token = bool(settings.TWICC_API_TOKEN)
    if not password_required:
        return JsonResponse({
            "authenticated": True,
            "password_required": False,
            "has_api_token": has_api_token,
        })

    authenticated = request.session.get("authenticated", False)
    return JsonResponse({
        "authenticated": authenticated,
        "password_required": True,
        "has_api_token": has_api_token,
    })


def login(request):
    """POST /api/auth/login/ - Authenticate with password.

    Body: {"password": "the_password"}

    The password is hashed with SHA-256 and compared to the stored hash
    using constant-time comparison to prevent timing attacks.

    On success, sets session["authenticated"] = True and returns 200.
    On failure, returns 401.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not settings.TWICC_PASSWORD_HASH:
        return JsonResponse({"error": "No password configured"}, status=400)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    password = data.get("password", "")
    password_hash = _hash_password(password)

    # Constant-time comparison to prevent timing attacks
    if hmac.compare_digest(password_hash, settings.TWICC_PASSWORD_HASH):
        request.session["authenticated"] = True
        logger.info("Successful login from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"authenticated": True})
    else:
        logger.warning("Failed login attempt from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"error": "Invalid password"}, status=401)


def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    request.session.flush()
    return JsonResponse({"authenticated": False})


# ── API Token Management ───────────────────────────────────────────────


def api_token(request):
    """GET /api/auth/token/ - Return the current API token.

    Requires session authentication (not token auth) to prevent
    token-to-token escalation. Returns 404 if no token configured.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # Require session auth — /api/auth/ is in PUBLIC_PATHS so middleware
    # doesn't enforce auth here; we must check explicitly.
    if settings.TWICC_PASSWORD_HASH and not request.session.get("authenticated"):
        return JsonResponse({"error": "Session authentication required"}, status=401)

    token = settings.TWICC_API_TOKEN
    if not token:
        return JsonResponse({"error": "No API token configured"}, status=404)

    return JsonResponse({"token": token})


def api_token_regenerate(request):
    """POST /api/auth/token/regenerate/ - Generate a new API token.

    Writes the new token to .env and updates the runtime setting.
    Existing connections using the old token will fail on next request.
    Requires session authentication.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if settings.TWICC_PASSWORD_HASH and not request.session.get("authenticated"):
        return JsonResponse({"error": "Session authentication required"}, status=401)

    new_token = secrets.token_hex(32)

    # Update .env file
    _update_env_token(new_token)

    # Update runtime setting
    settings.TWICC_API_TOKEN = new_token
    os.environ["TWICC_API_TOKEN"] = new_token

    logger.info("API token regenerated by %s", request.META.get("REMOTE_ADDR"))
    return JsonResponse({"token": new_token})


def _update_env_token(new_token: str) -> None:
    """Update or append TWICC_API_TOKEN in the .env file."""
    env_path = get_env_path()
    try:
        content = env_path.read_text() if env_path.exists() else ""
    except OSError:
        content = ""

    new_line = f"TWICC_API_TOKEN={new_token}"
    pattern = r"^TWICC_API_TOKEN=.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n{new_line}\n"

    env_path.write_text(content)
