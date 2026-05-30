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
    spawned_by_id: str | None = None,
) -> None:
    """List sessions as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    qs = Session.objects.filter(
        type="session",
        created_at__isnull=False,
        user_message_count__gt=0,
    ).order_by("-mtime")

    if not archived:
        qs = qs.filter(archived=False)

    if only_hidden:
        qs = qs.filter(hidden=True)
    elif not include_hidden:
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
