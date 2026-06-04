"""``twicc update-session <ID> annotations <OPERATION>...`` sub-command."""

from __future__ import annotations

import typer


def update_annotations_cmd(
    ctx: typer.Context,
    operations: list[str] = typer.Argument(
        ...,
        metavar="OPERATION...",
        help=(
            "Ordered annotation operation: clear, replace-file:PATH, "
            "merge-file:PATH, set:KEY=VALUE, or unset:KEY."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the annotation update may still apply "
            "on the server side."
        ),
    ),
) -> None:
    """Apply ordered annotation operations to the session."""
    session_id: str = ctx.obj

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.annotations import parse_annotation_update_operations
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

    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    parsed_operations, errors = parse_annotation_update_operations(operations)
    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "operations": parsed_operations,
    }

    drop = write_drop_file(payload, kind="session:update_annotations")

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
