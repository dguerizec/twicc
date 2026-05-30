"""``twicc update-session <ID> archive | unarchive`` sub-commands.

Two commands sharing the same plumbing — they only differ by the boolean
they put in the ``kind="session:update_archived"`` payload. Both apply the same
behaviour the UI does:

- ``archive``  → ``archived=True``: server marks the session archived,
  kills the live agent (``reason="archived"``), tears down any tmux
  terminal attached to the session, and — when ``autoUnpinOnArchive``
  is set in the synced settings AND the session is currently pinned —
  also unpins.
- ``unarchive`` → ``archived=False``: server simply flips the flag back.
  No agent restart is implied (the session stays cold until the user
  resumes it).

There are no options beyond the standard output / timeout controls;
auto-unpin is honoured server-side from the synced setting, no CLI
override.
"""

from __future__ import annotations

import typer


def _run_archived_update(
    session_id: str,
    *,
    archived: bool,
    timeout: int,
    no_color: bool,
    json_output: bool,
) -> None:
    """Drop a ``kind="session:update_archived"`` payload and wait for the status."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import (
        ServerDownError, check_heartbeat,
    )
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import (
        emit_final, emit_progress, emit_validation_errors,
    )
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Local pre-check: session must exist, not be a subagent, not be stale,
    # and have a project directory. The watcher-side service re-validates
    # the same conditions in case the DB state changed.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors(
            [ValidationError("SESSION_ID", e.code, e.message)],
            json_output=json_output,
        )
        raise typer.Exit(1)

    emit_progress(
        f"✓ Session {resolved.session_id!r} resolved "
        f"(provider: {resolved.provider}, project: {resolved.project_id})",
        json_output=json_output,
    )

    emit_progress(
        f"✓ {'Archive' if archived else 'Unarchive'} request prepared",
        json_output=json_output,
    )

    payload = {
        "session_id": resolved.session_id,
        "archived": archived,
    }

    drop = write_drop_file(payload, kind="session:update_archived")
    emit_progress(
        f"→ Request submitted (request_uuid: {drop.request_uuid[:8]}...)",
        json_output=json_output,
    )

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        json_output=json_output,
        timeout=timeout,
    )

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def update_archive_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the archive may still apply on the "
            "server side."
        ),
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable ANSI colors in human-readable output.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit a single JSON object on stdout instead of pretty text. "
            "Implies --no-color."
        ),
    ),
) -> None:
    """Archive the session.

    Same effect as the UI's archive action: sets ``archived=True`` on the
    row, kills any live agent attached to the session (``reason=archived``),
    tears down any tmux terminal in the ``s:<session_id>`` namespace, and —
    when the synced setting ``autoUnpinOnArchive`` is enabled and the
    session is currently pinned — also unpins. UI clients receive a
    ``session_updated`` broadcast carrying the final row state.
    """
    _run_archived_update(
        ctx.obj,
        archived=True,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )


def update_unarchive_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the unarchive may still apply on the "
            "server side."
        ),
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable ANSI colors in human-readable output.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit a single JSON object on stdout instead of pretty text. "
            "Implies --no-color."
        ),
    ),
) -> None:
    """Unarchive the session.

    Flips ``archived`` back to ``False``. Does not resume the agent — the
    session stays cold until you explicitly send a message or update its
    settings. UI clients receive a ``session_updated`` broadcast.
    """
    _run_archived_update(
        ctx.obj,
        archived=False,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )
