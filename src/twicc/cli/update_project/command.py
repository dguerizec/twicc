"""Top-level ``twicc update-project`` group.

Backward-compatible shape: the flat field patch (``--name`` / ``--color`` /
``--archive`` / ``--default-provider`` / trust flags) lives on the group
callback itself (``invoke_without_command=True``), so the historical
``twicc update-project <PROJECT> --name X`` keeps working unchanged, while
``settings`` is a real sub-command (``twicc update-project <PROJECT>
settings --provider P ...``) mirroring ``twicc update-session <ID> settings``.

The flat patch drops a ``kind="project:update"`` payload in
``<data_dir>/drop-requests/`` so the live TwiCC server applies it via
:func:`twicc.core.services.project_mutation.update_project_from_payload`
— validation + atomic update under the DB write lock +
``project_updated`` broadcast.

Mirrors the HTTP ``PUT /api/projects/<id>/`` endpoint: ``name``, ``color``,
``archived``, and ``default_provider`` are mutable; the ``directory`` is
immutable (the project id is derived from it). There is no
``delete-project`` counterpart by design.

The ``--trust`` / ``--untrust`` / ``--reset-trust`` flags are a separate,
**human-only** decision routed through a ``kind="project:trust"`` drop-file to
:func:`twicc.core.services.project_mutation.decide_project_trust_from_payload`
(the same ``decide_project_trust`` service the web dialog uses). They do not
combine with the field patch and are never exposed as an agent-facing skill.
"""

from __future__ import annotations

import typer
from typer.core import TyperGroup

from twicc.cli.update_project.settings_command import update_project_settings_cmd


class _FlatBackcompatGroup(TyperGroup):
    """Group whose own options may appear anywhere when no sub-command is invoked.

    A Click group parses its own options only up to the first positional
    token — everything after is reserved for the sub-command. That would
    break the historical flat form ``update-project <PROJECT> --name X``
    (options after the positional), which predates the group conversion.

    Sub-command intent is strictly positional here (``<PROJECT> <subcommand>
    ...``): when ``args[1]`` is not a known sub-command name, there is no
    sub-command in play, so the whole argv is parsed like a plain command
    (interspersed options allowed) and the flat flags land on the callback
    wherever they appear. When ``args[1]`` IS a sub-command, the default
    group parsing applies and the sub-command's options flow to it untouched.
    """

    def parse_args(self, ctx, args):
        if not (len(args) >= 2 and args[1] in self.commands):
            ctx.allow_interspersed_args = True
        return super().parse_args(ctx, args)


update_project_app = typer.Typer(
    name="update-project",
    cls=_FlatBackcompatGroup,
    help=(
        "Update an existing project: name, color, archived state, default "
        "provider (flat flags), or its per-provider agent-settings defaults "
        "(`settings` sub-command)."
    ),
    invoke_without_command=True,
)


@update_project_app.callback()
def update_project_main(
    ctx: typer.Context,
    project_id: str = typer.Argument(
        ...,
        metavar="PROJECT",
        help=(
            "Project to update. Either a project ID (slug form, with or "
            "without leading dash — same format as `twicc projects` output) "
            "or a directory path (absolute or relative); paths are resolved "
            "via realpath and converted to their canonical id."
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
    default_provider: str | None = typer.Option(
        None,
        "--default-provider",
        help=(
            "Provider a NEW session in this project defaults to (e.g. "
            "'claude_code', 'codex'). Inherited by sub-projects and git "
            "worktrees; unset = inherit from the parent chain, ultimately "
            "the global default. Mutually exclusive with "
            "`--unset-default-provider`."
        ),
    ),
    unset_default_provider: bool = typer.Option(
        False,
        "--unset-default-provider",
        help=(
            "Clear the project's default provider (back to inherit). "
            "Mutually exclusive with `--default-provider`."
        ),
    ),
    trust: bool = typer.Option(
        False,
        "--trust",
        help=(
            "Mark the project as trusted. A human-only decision: it does not "
            "combine with the other flags, nor with `--untrust` / "
            "`--reset-trust`."
        ),
    ),
    untrust: bool = typer.Option(
        False,
        "--untrust",
        help=(
            "Mark the project as untrusted — sessions created there fall under "
            "the restricted permission set. Mutually exclusive with `--trust` / "
            "`--reset-trust` and the field flags."
        ),
    ),
    reset_trust: bool = typer.Option(
        False,
        "--reset-trust",
        help=(
            "Clear the project's own trust decision so it inherits from its "
            "parent / git root. Mutually exclusive with `--trust` / `--untrust`."
        ),
    ),
    propagate: bool | None = typer.Option(
        None,
        "--propagate/--no-propagate",
        help=(
            "Whether the trust decision also covers sub-paths (only meaningful "
            "with `--trust`/`--untrust`; ignored on reset). Defaults to whether "
            "the project is under git."
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
) -> None:
    """Update an existing project's name, color, archived state, or default provider."""
    from twicc.cli._drop_request.project import derive_project_id
    from twicc.cli._output import emit_error

    # Accept a path or an id — derive the canonical project_id once,
    # before any DB lookup or validation downstream. Stashed in ``ctx.obj``
    # for the ``settings`` sub-command.
    project_id = derive_project_id(project_id)[0]
    ctx.obj = project_id

    # All flags above belong to the flat patch — they don't combine with a
    # sub-command (which has its own options). Reject loudly rather than
    # silently ignoring what the user typed.
    if ctx.invoked_subcommand is not None:
        passed = [n for n, on in (
            ("--name", new_name is not None), ("--unset-name", unset_name),
            ("--color", color is not None), ("--unset-color", unset_color),
            ("--archive", archive), ("--unarchive", unarchive),
            ("--default-provider", default_provider is not None),
            ("--unset-default-provider", unset_default_provider),
            ("--trust", trust), ("--untrust", untrust),
            ("--reset-trust", reset_trust),
            ("--propagate/--no-propagate", propagate is not None),
        ) if on]
        if passed:
            emit_error(
                f"Error: {', '.join(passed)} cannot be combined with the "
                f"'{ctx.invoked_subcommand}' sub-command.",
                code=64,
            )
        return

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.core.enums import Provider
    from twicc.core.models import Project
    from twicc.projects import validate_project_name_format
    from twicc.workspaces import validate_color

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    def _submit_and_exit(payload: dict, kind: str) -> None:
        drop = write_drop_file(payload, kind=kind)
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

    # Trust is a separate, human-only decision: it does not combine with the
    # name/color/archive patch, mirroring the web UI where it is its own action.
    # Handle it (and bail) before the field-patch path below.
    chosen_trust = [n for n, on in (
        ("--trust", trust), ("--untrust", untrust), ("--reset-trust", reset_trust),
    ) if on]
    if chosen_trust:
        trust_errors: list[ValidationError] = []
        if len(chosen_trust) > 1:
            trust_errors.append(ValidationError(
                chosen_trust[0], "conflicting_flags",
                f"{' / '.join(chosen_trust)} are mutually exclusive."))
        field_flags = [f for f, on in (
            ("--name", new_name is not None), ("--unset-name", unset_name),
            ("--color", color is not None), ("--unset-color", unset_color),
            ("--archive", archive), ("--unarchive", unarchive),
            ("--default-provider", default_provider is not None),
            ("--unset-default-provider", unset_default_provider),
        ) if on]
        if field_flags:
            trust_errors.append(ValidationError(
                chosen_trust[0], "conflicting_flags",
                f"Trust flags cannot be combined with {', '.join(field_flags)}; "
                "set trust in its own command."))
        if trust_errors:
            emit_validation_errors(trust_errors)
            raise typer.Exit(1)
        if not Project.objects.filter(id=project_id).exists():
            emit_validation_errors([ValidationError(
                "PROJECT", "project_not_found", f"Project {project_id!r} not found.")])
            raise typer.Exit(1)
        trusted_value = True if trust else (False if untrust else None)
        trust_payload: dict = {"project_id": project_id, "trusted": trusted_value}
        if trusted_value is not None and propagate is not None:
            trust_payload["propagation"] = propagate
        _submit_and_exit(trust_payload, "project:trust")

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
    if default_provider is not None and unset_default_provider:
        errors.append(ValidationError("--default-provider", "conflicting_flags",
                                       "--default-provider and --unset-default-provider "
                                       "cannot be used together."))

    # No-op check.
    has_patch = (
        new_name is not None
        or unset_name
        or color is not None
        or unset_color
        or archive
        or unarchive
        or default_provider is not None
        or unset_default_provider
    )
    if not has_patch:
        errors.append(ValidationError("update-project", "no_op",
                                       "Nothing to update — pass at least one --name / --unset-name / "
                                       "--color / --unset-color / --archive / --unarchive / "
                                       "--default-provider / --unset-default-provider."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Project existence + per-field validation.
    if not Project.objects.filter(id=project_id).exists():
        emit_validation_errors(
            [ValidationError("PROJECT", "project_not_found",
                              f"Project {project_id!r} not found.")],
        )
        raise typer.Exit(1)

    if not unset_name and new_name is not None:
        for e in validate_project_name_format(new_name, field="--name"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if not unset_color and color is not None:
        for e in validate_color(color, field="--color"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if default_provider is not None:
        valid_providers = sorted(p.value for p in Provider)
        if default_provider not in valid_providers:
            errors.append(ValidationError(
                "--default-provider", "invalid_provider",
                f"Unknown provider {default_provider!r}. Available: {valid_providers}.",
            ))

    if not unset_name and new_name is not None and not errors:
        trimmed = new_name.strip()
        if trimmed:
            collision = Project.objects.filter(name=trimmed).exclude(id=project_id).exists()
            if collision:
                errors.append(ValidationError("--name", "duplicate_name",
                                               f"Another project already uses the name {trimmed!r}."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

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
        "default_provider": default_provider,
        "unset_default_provider": unset_default_provider,
    }

    _submit_and_exit(payload, "project:update")


update_project_app.command(name="settings")(update_project_settings_cmd)
