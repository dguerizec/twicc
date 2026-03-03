"""Authentication and CSRF middleware.

When TWICC_PASSWORD_HASH is set, all requests (except login, static files)
require either an authenticated session or a valid API token. Unauthenticated
requests get a 401 response.

When TWICC_PASSWORD_HASH is empty/unset, all requests pass through (no protection).

OriginCheckMiddleware provides CSRF protection by verifying the Origin header
on mutation requests (POST, PUT, PATCH, DELETE).
"""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.http import JsonResponse

from twicc.auth.token import extract_bearer_token, verify_api_token

logger = logging.getLogger(__name__)

# HTTP methods that mutate state and need CSRF protection
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that are always accessible (no auth required)
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
)


class PasswordAuthMiddleware:
    """Middleware that enforces password authentication via session.

    Checks request.session["authenticated"] for all requests except
    public paths. Returns 401 JSON for API requests, 401 JSON for
    SPA requests (frontend handles redirect to login).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.password_required = bool(settings.TWICC_PASSWORD_HASH)
        if self.password_required:
            logger.info("Password protection enabled")
        else:
            logger.info("Password protection disabled (TWICC_PASSWORD_HASH not set)")

    def __call__(self, request):
        # No password configured = no protection
        if not self.password_required:
            return self.get_response(request)

        # Allow public paths
        if any(request.path.startswith(p) for p in PUBLIC_PATHS):
            return self.get_response(request)

        # Allow non-API paths (SPA catch-all serves index.html which contains
        # no sensitive data; Vue Router handles the login redirect client-side)
        if not request.path.startswith("/api/"):
            return self.get_response(request)

        # Check API token first (stateless, no session needed)
        bearer_token = extract_bearer_token(request)
        if bearer_token and verify_api_token(bearer_token):
            return self.get_response(request)

        # Fall back to session authentication
        if not request.session.get("authenticated"):
            return JsonResponse(
                {"error": "Authentication required"},
                status=401,
            )

        return self.get_response(request)


class OriginCheckMiddleware:
    """CSRF protection via Origin header validation.

    For unsafe HTTP methods (POST, PUT, PATCH, DELETE), rejects requests
    whose Origin header doesn't match the request's Host. This prevents
    cross-site form submissions and cross-origin fetch from other tabs.

    Requests authenticated via Bearer token bypass this check — they are
    programmatic API clients, not browser sessions vulnerable to CSRF.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in UNSAFE_METHODS:
            return self.get_response(request)

        # Bearer-authenticated requests are not CSRF-vulnerable
        bearer_token = extract_bearer_token(request)
        if bearer_token and verify_api_token(bearer_token):
            return self.get_response(request)

        origin = request.META.get("HTTP_ORIGIN")
        if not origin:
            # No Origin header — same-origin form posts and older browsers
            # may omit it.  Allow, since SameSite=Lax cookies already block
            # the main cross-site vector.
            return self.get_response(request)

        # Build the set of acceptable hosts from the request.
        # Behind a reverse proxy, Host may be the internal address while
        # X-Forwarded-Host carries the external domain.
        parsed = urlparse(origin)
        origin_netloc = parsed.netloc  # e.g. "twicc.guerizec.net"

        allowed_hosts = {request.get_host()}
        forwarded_host = request.META.get("HTTP_X_FORWARDED_HOST")
        if forwarded_host:
            # X-Forwarded-Host may contain multiple hosts (comma-separated)
            for host in forwarded_host.split(","):
                allowed_hosts.add(host.strip())

        if origin_netloc not in allowed_hosts:
            logger.warning(
                "CSRF rejected: Origin %s does not match hosts %s for %s %s",
                origin, allowed_hosts, request.method, request.path,
            )
            return JsonResponse(
                {"error": "Cross-origin request rejected"},
                status=403,
            )

        return self.get_response(request)
