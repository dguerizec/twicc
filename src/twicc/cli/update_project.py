"""``twicc update-project <PROJECT_ID> [OPTIONS]`` command.

Drops a ``kind="project:update"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server applies the patch via
:func:`twicc.core.services.project_mutation.update_project_from_payload`
— validation + atomic update under the DB write lock +
``project_updated`` broadcast.

Mirrors the HTTP ``PUT /api/projects/<id>/`` endpoint: only ``name``,
``color``, and ``archived`` are mutable; the ``directory`` is immutable
(the project id is derived from it). There is no ``delete-project``
counterpart by design.
"""

from __future__ import annotations

import typer


def update_project_cmd(
    project_id: str = typer.Argument(
        ...,
        metavar="PROJECT_ID",
        help=(
            "Id of the project to update (slug form, leading dash optional — "
            "auto-prepended if missing; same format as `twicc projects` "
            "output without the dash)."
        ),
    ),
    new_name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Set a new display name. Trimmed; ≤ 25 characters; globally "
            "unique. Mutually exclusive with `--unset-name`."
        ),
    ),
    unset_name: bool = typer.Option(
        False,
        "--unset-name",
        help=(
            "Clear the custom display name (the UI falls back to the "
            "directory's basename). Mutually exclusive with `--name`."
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
            "Clear the project's color. Mutually exclusive with `--color`."
        ),
    ),
    archive: bool = typer.Option(
        False,
        "--archive",
        help=(
            "Mark the project as archived (excluded from default listings). "
            "Mutually exclusive with `--unarchive`."
        ),
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help=(
            "Mark the project as not archived. Mutually exclusive with "
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
    """Update an existing project's name, color, and/or archived state."""
    # Auto-prepend the leading dash so callers can drop it (the convention
    # used by every other CLI command that takes a project_id, since the
    # raw form would otherwise be parsed as a flag by Typer).
    if not project_id.startswith("-"):
        project_id = f"-{project_id}"

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
    from twicc.projects import validate_project_name_format
    from twicc.workspaces import validate_color

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Mutually-exclusive flag checks (don't depend on DB state).
    errors: list[ValidationError] = []
    if new_name is not None and unset_name:
        errors.append(ValidationError("--name", "conflicting_flags",
                                       "--name and --unset-name cannot be used together."))
    if color is not None and unset_color:
        errors.append(ValidationError("--color", "conflicting_flags",
                                       "--color and --unset-color cannot be used together."))
    if archive and unarchive:
        errors.append(ValidationError("--archive", "conflicting_flags",
                                       "--archive and --unarchive cannot be used together."))

    # No-op check.
    has_patch = (
        new_name is not None
        or unset_name
        or color is not None
        or unset_color
        or archive
        or unarchive
    )
    if not has_patch:
        errors.append(ValidationError("update-project", "no_op",
                                       "Nothing to update — pass at least one --name / --unset-name / "
                                       "--color / --unset-color / --archive / --unarchive."))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    # Project existence + per-field validation.
    if not Project.objects.filter(id=project_id).exists():
        emit_validation_errors(
            [ValidationError("PROJECT_ID", "project_not_found",
                              f"Project {project_id!r} not found.")],
            json_output=json_output,
        )
        raise typer.Exit(1)

    if not unset_name and new_name is not None:
        for e in validate_project_name_format(new_name, field="--name"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if not unset_color and color is not None:
        for e in validate_color(color, field="--color"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if not unset_name and new_name is not None and not errors:
        trimmed = new_name.strip()
        if trimmed:
            collision = Project.objects.filter(name=trimmed).exclude(id=project_id).exists()
            if collision:
                errors.append(ValidationError("--name", "duplicate_name",
                                               f"Another project already uses the name {trimmed!r}."))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    emit_progress("✓ Pre-flight validation passed", json_output=json_output)

    if archive:
        archived_value: bool | None = True
    elif unarchive:
        archived_value = False
    else:
        archived_value = None

    payload = {
        "project_id": project_id,
        "name": new_name,
        "unset_name": unset_name,
        "color": color,
        "unset_color": unset_color,
        "archived": archived_value,
    }

    drop = write_drop_file(payload, kind="project:update")
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
