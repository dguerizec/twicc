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
) -> None:
    """List sessions as JSON to stdout.

    ``spawned_by`` is the raw CLI value (``None``, a session_id, or the
    literal ``"self"``) — it is resolved here, after ``django.setup()``,
    so callers don't need to bootstrap Django themselves.
    """
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import resolve_spawned_by_filter

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    qs = Session.objects.filter(
        type="session",
        created_at__isnull=False,
        user_message_count__gt=0,
    ).order_by("-mtime")

    if not archived:
        qs = qs.filter(archived=False)

    # When filtering by spawned_by, the caller is explicitly asking
    # about filiation — show every matching child whatever its
    # visibility. The hidden=False default only applies to unscoped
    # listings, where it keeps the output aligned with what the UI
    # displays. --only-hidden still narrows further if requested.
    if only_hidden:
        qs = qs.filter(hidden=True)
    elif not include_hidden and spawned_by_id is None:
        qs = qs.filter(hidden=False)

    if spawned_by_id is not None:
        qs = qs.filter(spawned_by_id=spawned_by_id)

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
