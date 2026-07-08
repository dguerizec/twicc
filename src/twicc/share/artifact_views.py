"""Artifact-share HTTP views. Always serves from the snapshot dir (D7); HTML gets
the trusted shell + CSP-wrapped inner doc; the broker proxy enforces the owner's
allowlist server-side (D6)."""

from __future__ import annotations

import os

from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.artifacts.broker_html import ARTIFACT_INNER_DOC_PATH
from twicc.share.headers import apply_share_headers
from twicc.share.html import (
    share_artifact_doc_response,
    share_artifact_shell_response,
    share_page_response,
)
from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


async def _ctx(request, token):
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return None, password_required_response(request, token)
    if ctx.share.kind != "artifact":
        raise Http404("This link is not available.")
    return ctx, None


def _snapshot_rel(ctx) -> str:
    """The bookmarked file's path RELATIVE to its own directory — i.e. its basename,
    since the snapshot copies the parent dir as the new root."""
    return os.path.basename(ctx.bookmark.relative_path)


async def share_artifact_page(request, ctx):
    """Root: HTML → shell page; markdown/mermaid → doc view (share-session bundle);
    other → the file directly. Counts a view."""
    from twicc.share.view_tracking import note_view
    from twicc.views import _guess_raw_content_type, _serve_artifact_file
    from twicc.core.services.share_mutation import confined_snapshot_path

    note_view(ctx.share, request)
    rel = _snapshot_rel(ctx)
    abs_root = confined_snapshot_path(ctx.share.id, rel)
    if abs_root is None:
        raise Http404("File not found")
    ext = os.path.splitext(rel)[1].lower()
    ctype = _guess_raw_content_type(abs_root)
    # Public display title (page header + recent-shares list): owner override, else
    # the real bookmark name, else the file's basename.
    display_title = (ctx.options.get("display_title") or "").strip()
    art_title = display_title or (ctx.bookmark.name if ctx.bookmark else None) or rel

    if ctype == "text/html":
        token_path = f"/share/{ctx.share.token}"
        return share_artifact_shell_response(
            token_path=token_path,
            inner_doc_url=f"{token_path}/{ARTIFACT_INNER_DOC_PATH}",
            snapshot_at=ctx.options.get("snapshot_at"),
            title=art_title,
        )
    if ext in (".md", ".markdown", ".mmd"):
        token_path = f"/share/{ctx.share.token}"
        meta = {"kind": "artifact", "docExt": ext.lstrip("."), "title": art_title,
                "docUrl": f"{token_path}/__twicc_raw__/{rel}", "snapshot_at": ctx.options.get("snapshot_at")}
        return share_page_response(token_path=token_path, meta=meta, mode="doc")
    response = await sync_to_async(_serve_artifact_file)(abs_root, as_document=False)
    if response is None:
        raise Http404("File not found")
    return apply_share_headers(response)


async def api_meta(request, token):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    return apply_share_headers(JsonResponse({"snapshot_at": ctx.options.get("snapshot_at")}))


async def share_artifact_doc(request, token):
    """``__twicc_doc__``: the artifact HTML wrapped (shim + CSP)."""
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    from twicc.core.services.share_mutation import confined_snapshot_path

    abs_path = confined_snapshot_path(ctx.share.id, _snapshot_rel(ctx))
    if abs_path is None or not os.path.isfile(abs_path):
        raise Http404("File not found")
    html = await sync_to_async(lambda: open(abs_path, "rb").read())()
    return share_artifact_doc_response(html)


async def share_artifact_asset(request, token, asset):
    """A sibling asset (or the raw doc for markdown), confined to the snapshot dir."""
    from twicc.core.services.share_mutation import confined_snapshot_path
    from twicc.views import _raw_file_response

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    # ``__twicc_raw__/<path>`` is the doc-view fetch of the raw markdown; strip it.
    if asset.startswith("__twicc_raw__/"):
        asset = asset[len("__twicc_raw__/"):]
    abs_path = confined_snapshot_path(ctx.share.id, asset)
    if abs_path is None:
        raise Http404("File not found")
    response = await sync_to_async(_raw_file_response)(abs_path)
    if response is None:
        raise Http404("File not found")
    return apply_share_headers(response)


async def share_artifact_proxy(request, token):
    """Broker proxy for a shared artifact (D6): server-enforces the owner's
    allowlist (unlike the owner proxy). Metadata block + IP pinning inherited."""
    from twicc.artifacts import proxy as artifact_proxy_mod

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    allowed = set((ctx.bookmark.allowed_hosts or {}).keys())
    return await artifact_proxy_mod.artifact_proxy(request, enforced_allowlist=allowed)
