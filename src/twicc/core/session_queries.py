"""Shared query/aggregation helpers over a session's items and tool-result links.

Both the owner REST views (``twicc.views``) and the public share views
(``twicc.share.session_views``) read the same underlying data; keeping the range
parsing, the tool-state aggregation and the subagent-link shaping here means the
two surfaces can never drift apart. Unlike ``core.serializers`` (pure, no DB),
the aggregation and slug-resolution helpers here run queries, so async callers
must wrap them in ``sync_to_async``.
"""

from __future__ import annotations

from django.db.models import Count, Max, Q

# The four aggregates that define a tool call's completion state. Kept as a single
# spec so every site that summarizes ``ToolResultLink`` rows stays in lockstep: the
# owner ``tool_states`` view, the share equivalent (both via ``aggregate_tool_states``)
# and the live compute broadcast in ``providers.compute_base``. ``Max`` on
# ``extra``/``error`` keeps the richest value regardless of arrival order (Codex
# emits several links per ``tool_use_id``).
TOOL_STATE_ANNOTATIONS = {
    "result_count": Count("id"),
    "completed_at": Max("tool_result_at"),
    "extra": Max("extra"),
    "error": Max("error"),
}


def parse_line_ranges(raw_ranges) -> Q | None:
    """Parse repeated ``range`` query values into a combined OR ``Q`` on ``line_num``.

    Each value is one of ``N`` (exact line), ``lo:hi`` (inclusive), ``lo:`` (from
    ``lo`` onward) or ``:hi`` (up to ``hi``). Malformed values are skipped. Returns
    ``None`` when nothing valid was parsed — callers turn that into their own
    400 / error response.
    """
    combined = None
    for r in raw_ranges:
        try:
            if ":" not in r:
                cond = Q(line_num=int(r))
            else:
                lo_str, hi_str = r.split(":", 1)
                lo = int(lo_str) if lo_str else None
                hi = int(hi_str) if hi_str else None
                if lo is not None and hi is not None:
                    cond = Q(line_num__gte=lo, line_num__lte=hi)
                elif lo is not None:
                    cond = Q(line_num__gte=lo)
                elif hi is not None:
                    cond = Q(line_num__lte=hi)
                else:
                    continue  # both bounds empty = invalid
        except ValueError:
            continue
        combined = cond if combined is None else (combined | cond)
    return combined


def aggregate_tool_states(links_qs) -> dict:
    """Summarize a ``ToolResultLink`` queryset into the per-tool state payload the
    frontend consumes: ``{tool_use_id: {result_count, completed_at, error, extra,
    tool_result_line_nums}}``.

    ``links_qs`` may be the full session set (owner view) or a
    visibility/ceiling-restricted subset (share view); the aggregation is identical
    either way. A tool with multiple links (Codex ``apply_patch`` / MCP / exec chains)
    exposes every ``tool_result_line_num``, not just the max. Synchronous — async
    callers wrap it in ``sync_to_async``.
    """
    aggregated = links_qs.values("tool_use_id").annotate(**TOOL_STATE_ANNOTATIONS)
    line_nums_by_tool: dict[str, list[int]] = {}
    for tool_use_id, line_num in (
        links_qs.order_by("tool_result_line_num").values_list("tool_use_id", "tool_result_line_num")
    ):
        line_nums_by_tool.setdefault(tool_use_id, []).append(line_num)
    return {
        entry["tool_use_id"]: {
            "result_count": entry["result_count"],
            "completed_at": entry["completed_at"].isoformat() if entry["completed_at"] else None,
            "error": entry["error"],
            "extra": entry["extra"],
            "tool_result_line_nums": line_nums_by_tool.get(entry["tool_use_id"], []),
        }
        for entry in aggregated
    }


def tool_results_payload(session, line_num, tool_id, max_line=None) -> dict:
    """Resolve the tool_result content(s) for one ``tool_use`` into
    ``{"results": [...]}``.

    Finds the linked tool_result line(s) via ``ToolResultLink``, loads those items —
    optionally clamped to ``max_line`` so a frozen share snapshot never leaks a
    post-freeze result — and runs the provider's ``get_tool_results`` extractor.
    Shared by the owner ``tool_results`` view and the share equivalent. Runs queries;
    async callers wrap it in ``sync_to_async``.
    """
    from twicc.core.models import SessionItem, ToolResultLink
    from twicc.providers.helpers import get_provider_helpers

    link_lines = list(
        ToolResultLink.objects.filter(
            session=session, tool_use_line_num=line_num, tool_use_id=tool_id
        ).values_list("tool_result_line_num", flat=True)
    )
    if not link_lines:
        return {"results": []}
    qs = SessionItem.objects.filter(session=session, line_num__in=link_lines)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    items = list(qs.order_by("line_num"))
    results = get_provider_helpers(session.provider).get_tool_results(items, tool_id)
    return {"results": results}


def serialize_agent_links(links) -> list[dict]:
    """Shape a list of already-fetched ``AgentLink`` rows into the subagent-link
    payload, resolving every spawned subagent's slug in one query.

    Shared by the owner ``subagents_state`` view and the share equivalent. ``slug``
    is ``None`` when the subagent file hasn't been parsed yet (race) or the provider
    carries no slug. Runs a query — async callers wrap it in ``sync_to_async``.
    """
    from twicc.core.models import Session

    subagents = {
        row[0]: row[1:]
        for row in Session.objects.filter(
            id__in=[link.agent_id for link in links]
        ).values_list("id", "slug", "last_stopped_at")
    }
    return [
        {
            "agent_id": link.agent_id,
            "agent_slug": subagents.get(link.agent_id, (None, None))[0],
            # When the subagent's own file says it went idle (see
            # ``BaseSessionCompute.subagent_turn_boundary``), this is the
            # moment it did. It lets a page reload decide "still running?"
            # without waiting for the parent tool chain to complete —
            # which, on Codex multi-agent v2, may simply never happen.
            "agent_stopped_at": (
                stopped.isoformat()
                if (stopped := subagents.get(link.agent_id, (None, None))[1])
                else None
            ),
            "tool_use_id": link.tool_use_id,
            "tool_use_line_num": link.tool_use_line_num,
            "is_background": link.is_background,
            "started_at": link.started_at.isoformat() if link.started_at else None,
        }
        for link in links
    ]
