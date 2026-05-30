"""``twicc create-project <DIRECTORY> [OPTIONS]`` command.

Drops a ``kind="project:create"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server creates the project via
:func:`twicc.core.services.project_mutation.create_project_from_payload`
— validation + ``register_project`` (single entry point that fires
``project_added`` broadcast + workspace auto-add).

The project id is **derived from the directory path** via
:func:`twicc.paths.path_to_project_id`; it cannot be set by the caller.
Creating a project for a directory that already has one is rejected.
"""

from __future__ import annotations

import os

import typer


def create_project_cmd(
    directory: str = typer.Argument(
        ...,
        metavar="DIRECTORY",
        help=(
            "Absolute (or resolvable) path of the project's working directory. "
            "The path is normalised via os.path.realpath, and the project id "
            "is derived from the canonical path."
        ),
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Optional display name. Trimmed; must be ≤ 25 characters and "
            "globally unique across projects. If omitted, the UI falls back "
            "to the directory's basename."
        ),
    ),
    color: str | None = typer.Option(
        None,
        "--color",
        help=(
            "Optional CSS hex color for the project badge (`#rgb`, `#rrggbb`, "
            "or `#rrggbbaa`)."
        ),
    ),
    create_directory: bool = typer.Option(
        False,
        "--create-directory",
        help=(
            "If the directory does not exist on disk, create it (and any "
            "missing parents) before registering the project. Without this "
            "flag, a missing directory is rejected with `directory_not_found`."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the project may still be created on "
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
    """Create a new project from a directory path."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os as _os
    _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
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
    from twicc.paths import path_to_project_id
    from twicc.projects import validate_project_name_format
    from twicc.workspaces import validate_color

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Resolve the directory to a canonical absolute path locally so the
    # pre-flight checks (existence, id collision) see the same path the
    # server will write. The server re-runs realpath as a trust-boundary
    # safety net.
    resolved = os.path.realpath(directory)
    errors: list[ValidationError] = []

    if not os.path.isabs(resolved):
        errors.append(ValidationError("DIRECTORY", "invalid_directory",
                                       f"Directory must be an absolute path (got {directory!r})."))
    elif os.path.exists(resolved):
        if not os.path.isdir(resolved):
            errors.append(ValidationError("DIRECTORY", "invalid_directory",
                                           f"Path {resolved!r} exists but is not a directory."))
    elif not create_directory:
        errors.append(ValidationError("DIRECTORY", "directory_not_found",
                                       f"Directory {resolved!r} does not exist. "
                                       "Pass --create-directory to create it."))

    # Format checks for name + color.
    for e in validate_project_name_format(name, field="--name"):
        errors.append(ValidationError(e.field, e.code, e.message))
    for e in validate_color(color, field="--color"):
        errors.append(ValidationError(e.field, e.code, e.message))

    # ID collision + name uniqueness — only when the directory itself
    # looks sane (no point spamming the user with a duplicate-id error if
    # the path is broken).
    if not errors:
        project_id = path_to_project_id(resolved)
        if Project.objects.filter(id=project_id).exists():
            errors.append(ValidationError("DIRECTORY", "project_already_exists",
                                           f"A project already exists for directory {resolved!r} "
                                           f"(id: {project_id!r})."))

        trimmed_name = name.strip() if name else None
        if trimmed_name and Project.objects.filter(name=trimmed_name).exists():
            errors.append(ValidationError("--name", "duplicate_name",
                                           f"Another project already uses the name {trimmed_name!r}."))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    emit_progress("✓ Pre-flight validation passed", json_output=json_output)

    payload = {
        "directory": resolved,
        "name": name,
        "color": color,
        "create_directory": create_directory,
    }

    drop = write_drop_file(payload, kind="project:create")
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
