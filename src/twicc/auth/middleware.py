"""Password authentication middleware.

When TWICC_PASSWORD_HASH is set, all requests (except login, static files)
require an authenticated session. Unauthenticated requests get a 401 response.

When TWICC_PASSWORD_HASH is empty/unset, all requests pass through (no protection).
"""

import logging

from asgiref.sync import markcoroutinefunction
from django.conf import settings
from django.http import JsonResponse

from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    SESSION_FINGERPRINT_KEY,
    is_session_authenticated,
)

logger = logging.getLogger(__name__)

# Paths that are always accessible (no auth required)
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
)

# Non-API URL prefixes whose responses leak data and therefore require an
# authenticated session. Anything not in this list and not under ``/api/``
# falls through to the SPA catch-all, which only serves ``index.html`` (no
# sensitive content) and lets the frontend redirect to login.
PROTECTED_NON_API_PREFIXES: tuple[str, ...] = (
    "/artifacts/",
)


class PasswordAuthMiddleware:
    """Middleware that enforces password authentication via session.

    Checks request.session["authenticated"] for all requests except
    public paths. Returns 401 JSON for API requests, 401 JSON for
    SPA requests (frontend handles redirect to login).
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        self.password_required = bool(settings.TWICC_PASSWORD_HASH)
        # Django/asgiref detect coroutine middleware via iscoroutinefunction
        # on the *instance*, not on cls.__call__. A class with ``async def
        # __call__`` is not enough — mark the instance explicitly.
        markcoroutinefunction(self)
        if self.password_required:
            logger.info("Password protection enabled")
        else:
            logger.info("Password protection disabled (TWICC_PASSWORD_HASH not set)")

    async def __call__(self, request):
        # No password configured = no protection
        if not self.password_required:
            return await self.get_response(request)

        # Allow public paths
        if any(request.path.startswith(p) for p in PUBLIC_PATHS):
            return await self.get_response(request)

        # Allow non-API paths (SPA catch-all serves index.html which contains
        # no sensitive data; Vue Router handles the login redirect client-side).
        # Non-API URLs that DO leak data (artifact serving, ...) are listed
        # in ``PROTECTED_NON_API_PREFIXES`` and fall through to the auth check.
        is_protected_non_api = any(
            request.path.startswith(prefix) for prefix in PROTECTED_NON_API_PREFIXES
        )
        if not request.path.startswith("/api/") and not is_protected_non_api:
            return await self.get_response(request)

        # Check session authentication for API requests. A session that was
        # logged in against an older password hash (rotated since) is treated
        # as unauthenticated, and its server-side row is dropped so it can't
        # be re-used by a parallel request.
        session = request.session
        auth_value = await session.aget(SESSION_AUTH_KEY)
        fingerprint = await session.aget(SESSION_FINGERPRINT_KEY)
        if not is_session_authenticated(
            auth_value,
            fingerprint,
            settings.TWICC_PASSWORD_HASH,
        ):
            if auth_value:
                await session.aflush()
            return JsonResponse(
                {"error": "Authentication required"},
                status=401,
            )

        return await self.get_response(request)
