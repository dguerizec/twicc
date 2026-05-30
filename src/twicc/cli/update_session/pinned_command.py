"""``twicc update-session <ID> pin <MODE> | unpin`` sub-commands.

Two boolean-ish flips sharing the same plumbing: ``pin`` takes a positional
``MODE`` argument (one of ``project`` / ``workspace`` / ``all``, mirroring
``PinMode``), ``unpin`` takes no argument. Both drop a
``kind="update_pinned"`` payload that the server resolves into a write on
``Session.pinned`` (NULL for unpin, the mode string otherwise) and a
``session_updated`` broadcast.

There are no options beyond the standard output / timeout controls; the
mode vocabulary is the same one the UI uses (no aliases, no shortcuts —
keeping every surface aligned).
"""

from __future__ import annotations

import typer


# Mirrors :class:`twicc.core.models.PinMode`. Kept here as a flat set so the
# CLI validates locally without importing Django (the lookup happens before
# ``django.setup()`` so the help / validation paths stay fast).
_VALID_PIN_MODES: tuple[str, ...] = ("project", "workspace", "all")


def _run_pinned_update(
    session_id: str,
    *,
    pinned: str | None,
    timeout: int,
    no_color: bool,
    json_output: bool,
) -> None:
    """Drop a ``kind="update_pinned"`` payload and wait for the status."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._session_request.discovery import (
        ServerDownError, check_heartbeat, get_data_dir,
    )
    from twicc.cli._session_request.drop_file import write_drop_file
    from twicc.cli._session_request.output import (
        emit_final, emit_progress, emit_validation_errors,
    )
    from twicc.cli._session_request.polling import poll_status
    from twicc.cli._session_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._session_request.validation import ValidationError

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

    label = "Unpin" if pinned is None else f"Pin (mode={pinned!r})"
    emit_progress(f"✓ {label} request prepared", json_output=json_output)

    payload = {
        "session_id": resolved.session_id,
        "pinned": pinned,
    }

    drop = write_drop_file(get_data_dir(), payload, kind="update_pinned")
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


def update_pin_cmd(
    ctx: typer.Context,
    mode: str = typer.Argument(
        ...,
        metavar="MODE",
        help=(
            "Pin scope: 'project' (only the session's project), 'workspace' "
            "(every workspace the project belongs to), or 'all' (every "
            "project — global pin). Same vocabulary as the UI's pin menu."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the pin may still apply on the "
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
    """Pin the session in one of three visibility scopes.

    Same effect as the UI's pin menu. A pinned session stays at the top of
    the sidebar in every matching context: ``project`` only in its own
    project; ``workspace`` in every workspace the project belongs to;
    ``all`` everywhere. Pinning an already-pinned session simply switches
    the scope. Use ``unpin`` to clear the pin.
    """
    # Local mode validation BEFORE Django setup so the user gets immediate
    # feedback for typos (e.g. ``pin workspce``).
    if mode not in _VALID_PIN_MODES:
        # Cheap fallback path: no Django, hand-roll the validation_error
        # envelope so the output shape matches every other CLI command.
        from twicc.cli._session_request.output import emit_validation_errors
        from twicc.cli._session_request.validation import ValidationError
        emit_validation_errors(
            [ValidationError(
                "MODE", "invalid_pin_mode",
                f"Unknown pin mode {mode!r}. Accepted: "
                f"{list(_VALID_PIN_MODES)}.",
            )],
            json_output=json_output,
        )
        raise typer.Exit(1)

    _run_pinned_update(
        ctx.obj,
        pinned=mode,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )


def update_unpin_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the unpin may still apply on the "
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
    """Unpin the session (regardless of the current pin scope).

    Same effect as picking "Not pinned" in the UI's pin menu. Idempotent:
    unpinning an already-unpinned session is a no-op write and still
    emits a ``session_updated`` broadcast.
    """
    _run_pinned_update(
        ctx.obj,
        pinned=None,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )
