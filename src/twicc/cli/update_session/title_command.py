"""``twicc update-session <ID> title "<NEW_TITLE>"`` sub-command.

Drops a ``kind="session:update_title"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server applies the change via
:func:`twicc.core.services.session_update.update_session_title_from_payload`
— DB write under the lock, full-text search reindex, provider-specific
``rename_session`` (Claude Code: JSONL custom-title entry, Codex:
``thread/name/set``), then ``session_updated`` broadcast.

No options beyond the standard ``--timeout`` control — title is the only
positional argument. Provider-specific validation (trim, non-empty,
≤ ``MAX_TITLE_LENGTH``) happens server-side via ``helpers.validate_title``.
"""

from __future__ import annotations

import typer


def update_title_cmd(
    ctx: typer.Context,
    new_title: str = typer.Argument(
        ...,
        metavar="NEW_TITLE",
        help=(
            "New session title. Trimmed; must be non-empty and ≤ 200 "
            "characters (the provider may impose a stricter cap)."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the rename may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Set a new title on the session.

    The new title is trimmed before validation; an empty (or whitespace-only)
    title is rejected with ``invalid_title``.
    """
    session_id: str = ctx.obj

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
    from twicc.cli._output import emit_error

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Local pre-check: session must exist, not be a subagent, not be stale,
    # and have a project directory. The watcher-side service re-validates
    # the same conditions in case the DB state changed.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    # Local title sanity: non-empty after trim. The full validation
    # (length, provider-specific rules) happens server-side via
    # ``helpers.validate_title``.
    if not new_title.strip():
        emit_validation_errors(
            [ValidationError(
                "NEW_TITLE", "invalid_title",
                "Title cannot be empty (or whitespace only).",
            )],
        )
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "title": new_title,
    }

    drop = write_drop_file(payload, kind="session:update_title")

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
