"""``twicc create-workspace <NAME> [OPTIONS]`` command.

Drops a ``kind="workspace:create"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server creates the workspace via
:func:`twicc.core.services.workspace_mutation.create_workspace_from_payload`
— validation + slug generation + atomic write under
``_workspaces_lock`` + ``workspaces_updated`` broadcast.

The CLI pre-validates name length, name uniqueness (against the current
on-disk snapshot), color format, and project-id existence so the user
gets immediate feedback for bad arguments without a server round-trip.
The server re-runs every check on its side since the drop-file is a
trust boundary.
"""

from __future__ import annotations

import typer


def create_workspace_cmd(
    name: str = typer.Argument(
        ...,
        metavar="NAME",
        help=(
            "Workspace name. Trimmed; must be non-empty, ≤ 20 characters, "
            "unique (case-insensitive) across existing workspaces. The id "
            "is derived from the name by slugifying (lowercased + non-"
            "alphanumeric replaced with `-`) with a `-2`/`-3` suffix on "
            "collision."
        ),
    ),
    color: str | None = typer.Option(
        None,
        "--color",
        help=(
            "Optional CSS hex color for the workspace badge "
            "(`#rgb`, `#rrggbb`, or `#rrggbbaa`)."
        ),
    ),
    add_projects: list[str] = typer.Option(
        [],
        "--add-project",
        help=(
            "Add a project to the workspace. Repeat for multiple projects. "
            "Each value is a project ID (with or without leading dash) or a "
            "directory path (absolute or relative); paths are resolved via "
            "realpath and converted to their canonical id. The resolved "
            "project must already exist in TwiCC (use `twicc projects` to "
            "list)."
        ),
    ),
    add_patterns: list[str] = typer.Option(
        [],
        "--add-pattern",
        help=(
            "Add an auto-add directory pattern (using `*` as wildcard). "
            "Newly detected projects whose directory matches a pattern are "
            "added to the workspace automatically. Repeat for multiple "
            "patterns."
        ),
    ),
    archived: bool = typer.Option(
        False,
        "--archived",
        help="Create the workspace in the archived state.",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the workspace may still be created on "
            "the server side."
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
    """Create a new workspace."""
    from twicc.cli._drop_request.project import derive_project_id

    # Accept paths or ids for each --add-project — derive the canonical
    # project_id once, before any DB lookup or validation downstream. The
    # derivation is pure (no DB), the existence check happens below.
    add_projects = [derive_project_id(value)[0] for value in add_projects]

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
    from twicc.core.models import Project
    from twicc.workspaces import (
        read_workspaces,
        validate_color,
        validate_pattern,
        validate_workspace_name,
    )

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Pre-flight validation. Each helper returns a list of WorkspaceMutationError;
    # we re-wrap as ValidationError so the output helpers' shape stays uniform.
    existing_workspaces = read_workspaces().get("workspaces", [])
    errors: list[ValidationError] = []

    name_errs = validate_workspace_name(name, existing_workspaces=existing_workspaces,
                                        field="NAME")
    color_errs = validate_color(color, field="--color")
    pattern_errs: list = []
    for p in add_patterns:
        pattern_errs.extend(validate_pattern(p, field="--add-pattern"))

    for e in (*name_errs, *color_errs, *pattern_errs):
        errors.append(ValidationError(e.field, e.code, e.message))

    # Project existence — query in bulk so we report every missing id at once.
    if add_projects:
        existing_project_ids = set(
            Project.objects.filter(id__in=add_projects).values_list("id", flat=True)
        )
        for pid in add_projects:
            if pid not in existing_project_ids:
                errors.append(ValidationError("--add-project", "project_not_found",
                                              f"Project {pid!r} not found."))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    emit_progress("✓ Pre-flight validation passed", json_output=json_output)

    payload = {
        "name": name,
        "color": color,
        "project_ids": list(add_projects),
        "auto_project_patterns": list(add_patterns),
        "archived": archived,
    }

    drop = write_drop_file(payload, kind="workspace:create")
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

    if outcome.status == "created":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
