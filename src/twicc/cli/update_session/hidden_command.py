"""``twicc update-session <ID> hide | unhide`` sub-commands.

Two commands sharing the same plumbing — they only differ by the
boolean they put in the ``kind="session:update_hidden"`` payload.

- ``hide``   → ``hidden=True``: the server runs the hidden-invariants
  pre-checks (permission_mode whitelist, question_widget != True) and
  rejects the request if the session can't satisfy them — the user
  must first switch the offending setting via ``update-session settings``.
  On success, the session is removed from every list / search / counter
  immediately (broadcast ``session_removed``); costs continue to flow
  into aggregates.
- ``unhide`` → ``hidden=False``: the server flips the flag back; the
  session re-enters the user surface (broadcast ``session_updated``).
  No invariant checks — the user is free to reconfigure
  ``permission_mode`` afterwards.

Pattern mirrors ``archived_command.py``; only the kind / labels change.
"""

from __future__ import annotations

import typer


def _run_hidden_update(
    session_id: str,
    *,
    hidden: bool,
    timeout: int,
) -> None:
    """Drop a ``kind="session:update_hidden"`` payload and wait for the status."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import (
        ServerDownError, check_heartbeat,
    )
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError

    try:
        check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    # Local pre-check: session must exist, not be a subagent, not be stale,
    # and have a project directory. The watcher-side service re-validates
    # the same conditions in case the DB state changed.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "hidden": hidden,
    }

    drop = write_drop_file(payload, kind="session:update_hidden")

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(outcome, request_uuid=drop.request_uuid, timeout=timeout)

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def update_hide_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the hide may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Hide the session.

    Removes the session from every user-visible list / search / counter
    while keeping costs in aggregates. Requires the session's
    permission_mode to be in the non-interactive whitelist and (Claude
    Code) question_widget=False — change those first via
    `twicc update-session <ID> settings` if needed.

    Connected clients receive a `session_removed` broadcast and drop the
    session from their store.
    """
    _run_hidden_update(
        ctx.obj,
        hidden=True,
        timeout=timeout,
    )


def update_unhide_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the unhide may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Unhide the session.

    Flips hidden back to False; the session reappears in every list /
    search / counter. Connected clients receive a `session_updated`
    broadcast and re-add it to their store. Counters and FTS are
    re-synced server-side.
    """
    _run_hidden_update(
        ctx.obj,
        hidden=False,
        timeout=timeout,
    )
