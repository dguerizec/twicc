"""Resolve explicit session ids plus optional scope filters to session ids."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q


def merge_session_scope_ids(
    session_ids: Iterable[str] | None = None,
    *,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
) -> list[str]:
    """Return explicit ids plus ids selected by filiation / annotation filters.

    Explicit ids keep caller order and come first. Scoped ids are appended in
    most-recent-session order, deduplicated against explicit ids.
    """
    from twicc.cli._drop_request.whoami import (
        resolve_descendants_filter,
        resolve_siblings_filter,
        resolve_spawn_tree_filter,
        resolve_spawned_by_filter,
    )
    from twicc.core.models import Session

    out: list[str] = []
    seen: set[str] = set()
    for sid in session_ids or ():
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)

    if not any((spawned_by, spawn_tree, descendants, siblings, annotation)):
        return out

    spawned_by_id = resolve_spawned_by_filter(spawned_by)
    spawn_root_id = resolve_spawn_tree_filter(spawn_tree)
    descendants_ids = resolve_descendants_filter(descendants)
    siblings_ids = resolve_siblings_filter(siblings)

    qs = Session.objects.filter(type="session")

    if spawned_by_id is not None:
        qs = qs.filter(spawned_by_id=spawned_by_id)

    if spawn_root_id is not None:
        qs = qs.filter(Q(spawn_root_id=spawn_root_id) | Q(pk=spawn_root_id))

    if descendants_ids is not None:
        qs = qs.filter(id__in=descendants_ids)

    if siblings_ids is not None:
        qs = qs.filter(id__in=siblings_ids)

    if annotation:
        from twicc.cli._annotation_filters import (
            apply_annotation_filters,
            parse_annotation_filter,
        )

        annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        qs = apply_annotation_filters(qs, annotation_filters)

    for sid in qs.order_by("-mtime").values_list("id", flat=True):
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)

    return out
