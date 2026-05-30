"""``twicc delete-workspace <ID>`` command.

Drops a ``kind="workspace:delete"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server removes the workspace via
:func:`twicc.core.services.workspace_mutation.delete_workspace_from_payload`
— atomic remove under ``_workspaces_lock`` + ``workspaces_updated``
broadcast.

No confirmation prompt by design — the CLI is meant to be scripted.
Projects referenced by the workspace are not deleted; they stay in TwiCC
and in any other workspace that lists them.
"""

from __future__ import annotations

import typer


def delete_workspace_cmd(
    workspace_id: str = typer.Argument(
        ...,
        metavar="WORKSPACE_ID",
        help="Id of the workspace to delete.",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the delete may still apply on the "
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
    """Delete a workspace by id."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import (
        emit_final, emit_progress, emit_validation_errors,
    )
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.workspaces import read_workspaces

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Pre-flight: workspace existence (cheap, gives the user a clean error
    # without waiting on the server). Re-checked under the lock server-side.
    existing_workspaces = read_workspaces().get("workspaces", [])
    if not any(w.get("id") == workspace_id for w in existing_workspaces):
        emit_validation_errors(
            [ValidationError("WORKSPACE_ID", "workspace_not_found",
                              f"Workspace {workspace_id!r} not found.")],
            json_output=json_output,
        )
        raise typer.Exit(1)

    emit_progress("✓ Pre-flight validation passed", json_output=json_output)

    payload = {"workspace_id": workspace_id}

    drop = write_drop_file(payload, kind="workspace:delete")
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

    if outcome.status == "deleted":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
