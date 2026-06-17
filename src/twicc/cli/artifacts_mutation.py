"""``twicc artifacts bookmark`` / ``twicc artifacts unbookmark`` implementations.

Both write a drop-request the live server resolves into a DB write +
``artifact_bookmark_updated`` / ``artifact_bookmark_removed`` broadcast, then
poll the status file — the exact plumbing of ``update-session pin/unpin``.

``bookmark`` upserts on the (session, path) key: it creates the bookmark or,
when one already exists, renames / re-scopes it (``kind="artifact_bookmark:upsert"``,
final status ``updated``). ``unbookmark`` removes it
(``kind="artifact_bookmark:delete"``, final status ``deleted``).
"""

from __future__ import annotations

import typer


def _run_drop(payload: dict, *, kind: str, success_status: str, timeout: int) -> None:
    """Drop a request, poll for its status, emit the final JSON, set exit code."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import emit_final
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._output import emit_error

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    drop = write_drop_file(payload, kind=kind)

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(outcome, request_uuid=drop.request_uuid, timeout=timeout)

    if outcome.status == success_status:
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def run_bookmark(*, session_id: str, path: str, name: str, scope: str | None, timeout: int) -> None:
    """Create or update an artifact bookmark.

    ``path`` is interpreted relative to the session's artifacts directory (the
    same value the listing prints as ``relative_path``), or absolute and confined
    to that directory — the server resolves and validates it. ``scope`` is
    ``None`` to keep the existing scope (or default to ``project`` on create).
    """
    _run_drop(
        {"session_id": session_id, "path": path, "name": name, "scope": scope},
        kind="artifact_bookmark:upsert",
        success_status="updated",
        timeout=timeout,
    )


def run_unbookmark(*, session_id: str, path: str, timeout: int) -> None:
    """Remove an artifact bookmark by its (session, path) key.

    Symmetric with ``bookmark``: the artifact file need not still exist on disk
    — only the bookmark row is removed.
    """
    _run_drop(
        {"session_id": session_id, "path": path},
        kind="artifact_bookmark:delete",
        success_status="deleted",
        timeout=timeout,
    )
