"""Session-share HTTP views. Thin wrappers: resolve context, then delegate to the
shared item-query helpers, applying the display ceiling + frozen line."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import (
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.share.display import filtered_items_qs, is_descendant_of
from twicc.share.headers import apply_share_headers
from twicc.share.html import share_page_response
from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


def _json(data, *, safe=True):
    return apply_share_headers(JsonResponse(data, safe=safe))


async def _ctx(request, token):
    """Resolve or translate the password/404 outcome into a response the caller
    returns directly. Returns (ctx, None) or (None, response)."""
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return None, password_required_response(request, token)
    if ctx.share.kind != "session":
        raise Http404("This link is not available.")
    return ctx, None


def _parse_ranges(request):
    q = []
    from django.db.models import Q
    for r in request.GET.getlist("range"):
        try:
            if ":" not in r:
                q.append(Q(line_num=int(r)))
            else:
                lo, hi = r.split(":", 1)
                lo = int(lo) if lo else None
                hi = int(hi) if hi else None
                if lo is not None and hi is not None:
                    q.append(Q(line_num__gte=lo, line_num__lte=hi))
                elif lo is not None:
                    q.append(Q(line_num__gte=lo))
                elif hi is not None:
                    q.append(Q(line_num__lte=hi))
        except ValueError:
            continue
    combined = None
    for cond in q:
        combined = cond if combined is None else (combined | cond)
    return combined


async def _snapshot_allowed_child_ids(ctx):
    """For a snapshot share, the set of subagent ids spawned by a ROOT tool_use at or
    before the frozen line. ``None`` = no snapshot clamp (everything allowed)."""
    frozen = ctx.options.get("frozen_at_line") if ctx.options.get("mode") == "snapshot" else None
    if frozen is None:
        return None
    from twicc.core.models import AgentLink

    return set(await sync_to_async(list)(
        AgentLink.objects.filter(session=ctx.session, tool_use_line_num__lte=frozen)
        .values_list("agent_id", flat=True)
    ))


async def _crosses_allowed_root_child(sub, root, allowed_ids, *, max_hops: int = 16) -> bool:
    """Walk ``sub``'s parent chain to ``root``; return True iff a node on the path is an
    allowed root child (a subagent spawned at/before the frozen line)."""
    from twicc.core.models import Session

    node = sub
    for _ in range(max_hops):
        if node.id in allowed_ids:
            return True
        parent_id = node.parent_session_id
        if parent_id is None:
            return False
        if parent_id == root.id:
            return node.id in allowed_ids
        node = await sync_to_async(lambda pid=parent_id: Session.objects.filter(id=pid).first())()
        if node is None:
            return False
    return False


async def _resolve_shared_subagent(ctx, sid):
    """Return the descendant subagent Session or raise 404. Only reachable when
    include_subagents is on. In snapshot mode a subagent spawned by a post-freeze
    root tool_use (directly, or as the ancestor of a nested subagent) stays hidden —
    subagents aren't line-clamped (design), but post-freeze ones must not leak."""
    from twicc.core.models import Session

    if not ctx.options.get("include_subagents", True):
        raise Http404("Not found")
    sub = await sync_to_async(lambda: Session.objects.filter(id=sid).first())()
    if sub is None or not await is_descendant_of(sub, ctx.session):
        raise Http404("Not found")
    allowed = await _snapshot_allowed_child_ids(ctx)
    if allowed is not None and not await _crosses_allowed_root_child(sub, ctx.session, allowed):
        raise Http404("Not found")
    return sub


# ── Page ────────────────────────────────────────────────────────────────────

async def share_session_page(request, ctx):
    from twicc.core.serializers import serialize_share_public_meta

    meta = await sync_to_async(serialize_share_public_meta)(ctx.share)
    # View tracking (Phase 8) — accumulate a page view.
    from twicc.share.view_tracking import note_view
    note_view(ctx.share, request)
    return share_page_response(token_path=f"/share/{ctx.share.token}", meta=meta)


# ── API ─────────────────────────────────────────────────────────────────────

async def share_session_meta(request, token):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    from twicc.core.serializers import serialize_share_public_meta
    return _json(await sync_to_async(serialize_share_public_meta)(ctx.share))


def _ceiling_kwargs(ctx, session):
    opts = ctx.options
    max_line = None
    if opts.get("mode") == "snapshot":
        # Fail closed: a snapshot must never serve the live tail. If the freeze point
        # is somehow missing (malformed share), fall back to the session's last_line
        # rather than serving unbounded live content.
        frozen = opts.get("frozen_at_line")
        max_line = frozen if frozen is not None else session.last_line
    # Subagents are never frozen-line clamped (frozen line applies to the root).
    if session.id != ctx.session.id:
        max_line = None
    return {"max_display_mode": opts.get("max_display_mode", "normal"), "max_line": max_line}


async def _items_metadata(request, token, subagent_id=None):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    qs = filtered_items_qs(session, **_ceiling_kwargs(ctx, session)).defer("content").order_by("line_num")
    items = await sync_to_async(list)(qs)
    return _json([serialize_session_item_metadata(i) for i in items], safe=False)


async def _items(request, token, subagent_id=None):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    ranges = _parse_ranges(request)
    if ranges is None:
        return _json({"error": "At least one 'range' query parameter is required"}, safe=True)
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    qs = filtered_items_qs(session, extra=ranges, **_ceiling_kwargs(ctx, session)).order_by("line_num")
    items = await sync_to_async(list)(qs)
    return _json([serialize_session_item(i) for i in items], safe=False)


async def _tool_results(request, token, line_num, tool_id, subagent_id=None):
    from twicc.core.models import SessionItem, ToolResultLink
    from twicc.providers.helpers import get_provider_helpers

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    link_lines = await sync_to_async(list)(
        ToolResultLink.objects.filter(session=session, tool_use_line_num=line_num, tool_use_id=tool_id)
        .values_list("tool_result_line_num", flat=True)
    )
    if not link_lines:
        return _json({"results": []})
    kw = _ceiling_kwargs(ctx, session)
    # Tool-result rows are always at/under the ceiling for a visible tool_use, but
    # clamp defensively so a frozen snapshot never leaks a post-freeze result.
    qs = SessionItem.objects.filter(session=session, line_num__in=link_lines)
    if kw["max_line"] is not None:
        qs = qs.filter(line_num__lte=kw["max_line"])
    items = await sync_to_async(list)(qs.order_by("line_num"))
    results = get_provider_helpers(session.provider).get_tool_results(items, tool_id)
    return _json({"results": results})


# A tool_result line carrying an originalFile can be large; skip past this and the
# viewer falls back to the input-based diff (matches the owner's "file too large").
_MAX_BACKEND_PATCH_BYTES = 1_000_000


async def _result_items(request, token, tool_id, subagent_id=None):
    """Raw content of the tool_result line(s) backing one tool call, ceiling-EXEMPT
    (gated by ``ToolResultLink`` like tool-results) but frozen-clamped. The full-file
    Edit / apply_patch diff needs ``structuredPatch`` + ``originalFile`` (Claude) or
    ``original_files`` (Codex), which live in a DEBUG_ONLY line the display ceiling
    drops from ``/items``. Provider-agnostic: returns ``serialize_session_item`` and
    lets the reused per-provider frontend extract — no Claude/Codex logic here."""
    from twicc.core.models import SessionItem, ToolResultLink

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    link_lines = await sync_to_async(list)(
        ToolResultLink.objects.filter(session=session, tool_use_id=tool_id)
        .values_list("tool_result_line_num", flat=True)
    )
    if not link_lines:
        return _json([], safe=False)
    max_line = _ceiling_kwargs(ctx, session)["max_line"]
    qs = SessionItem.objects.filter(session=session, line_num__in=link_lines)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    items = await sync_to_async(list)(qs.order_by("line_num"))
    out = [serialize_session_item(i) for i in items if len(i.content or "") <= _MAX_BACKEND_PATCH_BYTES]
    return _json(out, safe=False)


async def _tool_states(request, token, subagent_id=None):
    """Mirror of the owner's ``views.tool_states`` aggregation, restricted to links
    whose tool_use is a VISIBLE item (ceiling + frozen line) — a hidden tool_use
    must not leak its ``error``/``extra`` through the state endpoint. In a frozen
    snapshot, results landed after the freeze don't exist for viewers either."""
    from django.db.models import Count, Max
    from twicc.core.models import ToolResultLink

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    kw = _ceiling_kwargs(ctx, session)
    visible_lines = filtered_items_qs(session, **kw).values("line_num")
    links_qs = ToolResultLink.objects.filter(session=session, tool_use_line_num__in=visible_lines)
    if kw["max_line"] is not None:
        links_qs = links_qs.filter(tool_result_line_num__lte=kw["max_line"])
    links = await sync_to_async(list)(
        links_qs.values("tool_use_id").annotate(
            result_count=Count("id"),
            completed_at=Max("tool_result_at"),
            extra=Max("extra"),
            error=Max("error"),
        )
    )
    line_nums_by_tool: dict[str, list[int]] = {}
    for tool_use_id, line_num in await sync_to_async(list)(
        links_qs.order_by("tool_result_line_num").values_list("tool_use_id", "tool_result_line_num")
    ):
        line_nums_by_tool.setdefault(tool_use_id, []).append(line_num)
    tools = {
        entry["tool_use_id"]: {
            "result_count": entry["result_count"],
            "completed_at": entry["completed_at"].isoformat() if entry["completed_at"] else None,
            "error": entry["error"],
            "extra": entry["extra"],
            "tool_result_line_nums": line_nums_by_tool.get(entry["tool_use_id"], []),
        }
        for entry in links
    }
    return _json({"tools": tools})


async def share_session_subagents(request, token):
    from twicc.core.models import AgentLink, Session

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    if not ctx.options.get("include_subagents", True):
        return _json([], safe=False)
    links_qs = AgentLink.objects.filter(session=ctx.session)
    # Snapshot: never disclose subagents spawned by a post-freeze root tool_use.
    frozen = ctx.options.get("frozen_at_line") if ctx.options.get("mode") == "snapshot" else None
    if frozen is not None:
        links_qs = links_qs.filter(tool_use_line_num__lte=frozen)
    links = await sync_to_async(list)(links_qs.order_by("id"))
    slugs = dict(await sync_to_async(list)(
        Session.objects.filter(id__in=[link.agent_id for link in links]).values_list("id", "slug")
    ))
    return _json([
        {
            "agent_id": link.agent_id,
            "agent_slug": slugs.get(link.agent_id),
            "tool_use_id": link.tool_use_id,
            "tool_use_line_num": link.tool_use_line_num,
            "is_background": link.is_background,
            "started_at": link.started_at.isoformat() if link.started_at else None,
        }
        for link in links
    ], safe=False)


# Public entry points (URL-mapped). Method-guarded, delegating to the helpers above.

async def api_meta(request, token):
    return await share_session_meta(request, token)


async def api_items_metadata(request, token):
    return await _items_metadata(request, token)


async def api_items(request, token):
    return await _items(request, token)


async def api_tool_results(request, token, line_num, tool_id):
    return await _tool_results(request, token, line_num, tool_id)


async def api_tool_states(request, token):
    return await _tool_states(request, token)


async def api_backend_patch(request, token, tool_id):
    return await _result_items(request, token, tool_id)


async def api_subagents(request, token):
    return await share_session_subagents(request, token)


async def api_subagent_items_metadata(request, token, subagent_id):
    return await _items_metadata(request, token, subagent_id)


async def api_subagent_items(request, token, subagent_id):
    return await _items(request, token, subagent_id)


async def api_subagent_tool_results(request, token, subagent_id, line_num, tool_id):
    return await _tool_results(request, token, line_num, tool_id, subagent_id)


async def api_subagent_tool_states(request, token, subagent_id):
    return await _tool_states(request, token, subagent_id)


async def api_subagent_backend_patch(request, token, subagent_id, tool_id):
    return await _result_items(request, token, tool_id, subagent_id)


# ── Inline transcript media (design §6.2) ───────────────────────────────────

async def share_session_media(request, token, filename):
    """Serve an inline artifact image referenced by the transcript, confined to the
    shared session's artifacts dir + the extension allowlist (copied contract from
    ``views.session_artifact``, keyed by token)."""
    import stat

    from django.http import FileResponse
    from twicc.paths import get_session_artifacts_dir
    from twicc.views import _classify_artifact_filename

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    content_type = _classify_artifact_filename(filename)
    if content_type is None:
        raise Http404("Artifact not found")
    artifacts_dir = get_session_artifacts_dir(ctx.session.id)
    target = artifacts_dir / filename

    def _open():
        try:
            root = artifacts_dir.resolve(strict=True)
            resolved = target.resolve(strict=True)
        except (FileNotFoundError, RuntimeError, OSError):
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        try:
            if not stat.S_ISREG(resolved.stat().st_mode):
                return None
            return resolved.open("rb")
        except OSError:
            return None

    fp = await sync_to_async(_open)()
    if fp is None:
        raise Http404("Artifact not found")
    return apply_share_headers(FileResponse(fp, content_type=content_type, as_attachment=False))
