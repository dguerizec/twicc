"""CLI implementation for the ``twicc sessions`` subcommand."""

import sys

import orjson


def main(
    *,
    project: str | None = None,
    workspace: str | None = None,
    limit: int = 20,
    offset: int = 0,
    archived: bool = False,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_root: str | None = None,
    descendants: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """List sessions as JSON to stdout.

    ``spawned_by``, ``spawn_root`` and ``descendants`` are raw CLI values
    (``None``, a session_id, or the literal ``"self"``) — they are resolved
    here, after ``django.setup()``, so callers don't need to bootstrap
    Django themselves. The typer wrapper guarantees they are mutually
    exclusive.
    """
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import (
        resolve_descendants_filter,
        resolve_spawn_root_filter,
        resolve_spawned_by_filter,
    )

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
        spawn_root_id = resolve_spawn_root_filter(spawn_root)
        descendants_ids = resolve_descendants_filter(descendants)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    from django.db.models import Q

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    qs = Session.objects.filter(
        type="session",
        created_at__isnull=False,
        user_message_count__gt=0,
    ).order_by("-mtime")

    if not archived:
        qs = qs.filter(archived=False)

    # When filtering by spawned_by / spawn_root / descendants, the caller is
    # explicitly asking about filiation — show every matching session in the
    # tree whatever its visibility. The hidden=False default only applies to
    # unscoped listings, where it keeps the output aligned with what the
    # UI displays. --only-hidden still narrows further if requested.
    if only_hidden:
        qs = qs.filter(hidden=True)
    elif (
        not include_hidden
        and spawned_by_id is None
        and spawn_root_id is None
        and descendants_ids is None
    ):
        qs = qs.filter(hidden=False)

    if spawned_by_id is not None:
        qs = qs.filter(spawned_by_id=spawned_by_id)

    if spawn_root_id is not None:
        # OR with Q(pk=spawn_root_id) to also include the root of a single-node
        # tree (a standalone session that has never spawned a child still has
        # spawn_root_id=NULL, so the plain equality filter would exclude it).
        # Same pattern as ``twicc topology`` (cf. ``cli/topology.py``).
        qs = qs.filter(Q(spawn_root_id=spawn_root_id) | Q(pk=spawn_root_id))

    if descendants_ids is not None:
        # An empty set means "the target has no descendants"; ``id__in=[]``
        # returns nothing without hitting the DB, which is exactly what we want.
        qs = qs.filter(id__in=descendants_ids)

    if annotation:
        from twicc.cli._annotation_filters import apply_annotation_filters, parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        qs = apply_annotation_filters(qs, annotation_filters)

    if workspace is not None:
        from twicc.workspaces import read_workspaces

        ws = next(
            (w for w in read_workspaces().get("workspaces", []) if w.get("id") == workspace),
            None,
        )
        if ws is None:
            print(f"Error: workspace '{workspace}' not found.", file=sys.stderr)
            sys.exit(1)
        qs = qs.filter(project_id__in=ws.get("projectIds", []))

    if project is not None:
        qs = qs.filter(project_id=project)

    sessions = qs[offset : offset + limit]
    data = [serialize_session(s) for s in sessions]

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
