"""Dispatch ``/share/<token>/`` (and its bottom ``<path:asset>`` catch) by the
share's kind, so one flat URL space serves both session and artifact shares."""

from __future__ import annotations

from django.http import HttpResponseNotAllowed

from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


async def share_recent(request):
    """``/share/`` (no token): the share host homepage — a client-side list of the
    shares this browser has opened (localStorage, design §12). No server data."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    from twicc.share.html import share_recent_response
    return share_recent_response()


async def share_root(request, token):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return password_required_response(request, token)
    if ctx.share.kind == "session":
        from twicc.share.session_views import share_session_page
        return await share_session_page(request, ctx)
    from twicc.share.artifact_views import share_artifact_page
    return await share_artifact_page(request, ctx)


async def share_asset_or_doc(request, token, asset):
    """Bottom catch for artifact shares: ``__twicc_doc__`` → wrapped inner doc,
    anything else → a sibling asset. Session shares have no such sub-paths → 404."""
    from twicc.artifacts.broker_html import ARTIFACT_INNER_DOC_PATH
    from twicc.share.artifact_views import share_artifact_asset, share_artifact_doc

    if asset == ARTIFACT_INNER_DOC_PATH:
        return await share_artifact_doc(request, token)
    return await share_artifact_asset(request, token, asset)
