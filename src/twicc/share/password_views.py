"""Standalone per-link password page (design §7.1). Mirrors ``auth.views.artifact_auth``
and shares its IP rate limiter."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from twicc.auth.hashers import verify_password
from twicc.auth.views import _check_rate_limit, _get_client_ip, _login_attempts, _record_failed_attempt
from twicc.core.services.share_tokens import aresolve_share, password_fingerprint
from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY


def _safe_redirect(token: str, value: str) -> str:
    """Confine the post-login redirect to this share's own path."""
    prefix = f"/share/{token}/"
    if value and value.startswith(prefix) and not value.startswith("//"):
        return value
    return prefix


async def share_auth(request, token):
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)

    share = await aresolve_share(token)
    if share is None or not share.is_active() or not share.password_hash:
        # No password (or gone): send them to the share root (uniform behaviour).
        return HttpResponseRedirect(f"/share/{token}/")

    if request.method == "GET":
        target = _safe_redirect(token, request.GET.get("redirect", ""))
        return render(request, "share_auth.html", {"redirect": target, "error": False})

    body = parse_qs(request.body.decode("utf-8", "replace"))
    target = _safe_redirect(token, (body.get("redirect") or [""])[0])
    ip = _get_client_ip(request)
    wait = _check_rate_limit(ip)
    if wait is not None:
        return render(request, "share_auth.html", {"redirect": target, "error": True}, status=429)
    password = (body.get("password") or [""])[0]
    if await asyncio.to_thread(verify_password, password, share.password_hash):
        grants = await request.session.aget(SHARE_GRANTS_SESSION_KEY) or {}
        grants[share.id] = password_fingerprint(share.password_hash)
        await request.session.aset(SHARE_GRANTS_SESSION_KEY, grants)
        _login_attempts.pop(ip, None)
        return HttpResponseRedirect(target)
    _record_failed_attempt(ip)
    return render(request, "share_auth.html", {"redirect": target, "error": True}, status=401)
