"""Server-side content filtering for shared sessions (design §6.2). The display
ceiling and frozen line are enforced in SQL so nothing above them ever reaches
the viewer's network tab."""

from __future__ import annotations

from django.db.models import Q

# ItemDisplayLevel: ALWAYS=1, COLLAPSIBLE=2, DEBUG_ONLY=3.
# A max_display_mode caps which levels are visible. Only "debug" exposes level 3.
_CEILING = {
    "conversation": 2,
    "simplified": 2,
    "normal": 2,
    "debug": 3,
}


def display_ceiling(max_display_mode: str) -> int:
    return _CEILING.get(max_display_mode, 2)


def filtered_items_qs(session, *, max_display_mode: str, max_line: int | None, extra: Q | None = None):
    """Base queryset for a shared session's items, ceiling- and frozen-line-filtered.
    ``display_level`` NULL rows (uncomputed) are excluded except in debug (they'd
    only be visible there anyway)."""
    ceiling = display_ceiling(max_display_mode)
    qs = session.items.all()
    if ceiling < 3:
        qs = qs.filter(display_level__isnull=False, display_level__lte=ceiling)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    if extra is not None:
        qs = qs.filter(extra)
    return qs


async def is_descendant_of(candidate, root, *, max_hops: int = 16) -> bool:
    """Whether ``candidate`` is a subagent descendant of ``root`` — walk
    ``parent_session`` up to ``max_hops`` (with a ``spawn_root`` shortcut)."""
    from asgiref.sync import sync_to_async

    if candidate.id == root.id:
        return False
    if candidate.spawn_root_id and candidate.spawn_root_id == root.id:
        return True
    node = candidate
    for _ in range(max_hops):
        parent_id = node.parent_session_id
        if parent_id is None:
            return False
        if parent_id == root.id:
            return True
        node = await sync_to_async(lambda pid=parent_id: type(root).objects.filter(id=pid).first())()
        if node is None:
            return False
    return False
