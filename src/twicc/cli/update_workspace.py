"""``twicc update-workspace <ID> [OPTIONS]`` command.

Drops a ``kind="workspace:update"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server applies the patch via
:func:`twicc.core.services.workspace_mutation.update_workspace_from_payload`
— validation + atomic read-modify-write under ``_workspaces_lock`` +
``workspaces_updated`` broadcast.

Flags are combinable: every operation specified is applied in the same
atomic write. At least one effective patch must be supplied (no flag at
all → ``no_op`` error). ``--color`` and ``--unset-color`` are mutually
exclusive; ``--archive`` and ``--unarchive`` are mutually exclusive.

Add/remove on projects and patterns are idempotent (silently skip
already-present additions and already-absent removals), matching the
auto-add helper's semantics.
"""

from __future__ import annotations

import typer


def update_workspace_cmd(
    workspace_id: str = typer.Argument(
        ...,
        metavar="WORKSPACE_ID",
        help="Id of the existing workspace to update.",
    ),
    new_name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Rename the workspace. Trimmed; must be non-empty, ≤ 20 "
            "characters, unique (case-insensitive). The workspace's id "
            "stays immutable — only the display name changes."
        ),
    ),
    color: str | None = typer.Option(
        None,
        "--color",
        help=(
            "Set a CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). "
            "Mutually exclusive with `--unset-color`."
        ),
    ),
    unset_color: bool = typer.Option(
        False,
        "--unset-color",
        help=(
            "Clear the workspace's color. Mutually exclusive with `--color`."
        ),
    ),
    add_projects: list[str] = typer.Option(
        [],
        "--add-project",
        help=(
            "Add a project to the workspace (idempotent). Repeat for "
            "multiple projects. Each project id must exist in TwiCC."
        ),
    ),
    remove_projects: list[str] = typer.Option(
        [],
        "--remove-project",
        help=(
            "Remove a project from the workspace (idempotent — silently "
            "skips projects already absent). Repeat for multiple projects."
        ),
    ),
    add_patterns: list[str] = typer.Option(
        [],
        "--add-pattern",
        help=(
            "Add an auto-add directory pattern (idempotent). Repeat for "
            "multiple patterns."
        ),
    ),
    remove_patterns: list[str] = typer.Option(
        [],
        "--remove-pattern",
        help=(
            "Remove an auto-add directory pattern (idempotent). Repeat for "
            "multiple patterns."
        ),
    ),
    archive: bool = typer.Option(
        False,
        "--archive",
        help=(
            "Mark the workspace as archived. Mutually exclusive with "
            "`--unarchive`."
        ),
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help=(
            "Mark the workspace as not archived. Mutually exclusive with "
            "`--archive`."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply on the "
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
    """Update an existing workspace."""
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

    # Mutually-exclusive flag checks first — they don't depend on disk state.
    errors: list[ValidationError] = []
    if color is not None and unset_color:
        errors.append(ValidationError("--color", "conflicting_flags",
                                       "--color and --unset-color cannot be used together."))
    if archive and unarchive:
        errors.append(ValidationError("--archive", "conflicting_flags",
                                       "--archive and --unarchive cannot be used together."))

    # No-op check: at least one effective field must be touched.
    has_patch = (
        new_name is not None
        or color is not None
        or unset_color
        or bool(add_projects)
        or bool(remove_projects)
        or bool(add_patterns)
        or bool(remove_patterns)
        or archive
        or unarchive
    )
    if not has_patch:
        errors.append(ValidationError("update-workspace", "no_op",
                                       "Nothing to update — pass at least one --name / --color / "
                                       "--unset-color / --add-project / --remove-project / "
                                       "--add-pattern / --remove-pattern / --archive / --unarchive."))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    # Workspace existence + per-field validation against the current snapshot.
    existing_workspaces = read_workspaces().get("workspaces", [])
    if not any(w.get("id") == workspace_id for w in existing_workspaces):
        emit_validation_errors(
            [ValidationError("WORKSPACE_ID", "workspace_not_found",
                              f"Workspace {workspace_id!r} not found.")],
            json_output=json_output,
        )
        raise typer.Exit(1)

    if new_name is not None:
        name_errs = validate_workspace_name(
            new_name,
            existing_workspaces=existing_workspaces,
            current_id=workspace_id,
            field="--name",
        )
        for e in name_errs:
            errors.append(ValidationError(e.field, e.code, e.message))

    if color is not None and not unset_color:
        for e in validate_color(color, field="--color"):
            errors.append(ValidationError(e.field, e.code, e.message))

    for p in add_patterns:
        for e in validate_pattern(p, field="--add-pattern"):
            errors.append(ValidationError(e.field, e.code, e.message))

    # Project existence — only check the additions; silent removals don't
    # need DB validation (they're idempotent against missing ids).
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

    archived_value: bool | None
    if archive:
        archived_value = True
    elif unarchive:
        archived_value = False
    else:
        archived_value = None

    payload = {
        "workspace_id": workspace_id,
        "name": new_name,
        "color": color,
        "unset_color": unset_color,
        "add_projects": list(add_projects),
        "remove_projects": list(remove_projects),
        "add_patterns": list(add_patterns),
        "remove_patterns": list(remove_patterns),
        "archived": archived_value,
    }

    drop = write_drop_file(payload, kind="workspace:update")
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
