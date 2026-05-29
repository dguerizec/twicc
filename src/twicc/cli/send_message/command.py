"""Top-level ``twicc send-message`` command."""

from __future__ import annotations

import typer


def send_message_cmd(
    session_id: str = typer.Argument(
        ...,
        help="Id of the existing session to send the message to.",
    ),
    prompt: str = typer.Argument(
        ...,
        help="Message text, or path to a file whose content is the message.",
    ),
    attach: list[str] = typer.Option(
        [],
        "--attach",
        help=(
            "Path to a file to attach (repeatable). Claude Code accepts "
            "PNG/JPEG/GIF/WebP/PDF/text/plain up to 5 MB each. Codex accepts "
            "images only. Max 100 files, 32 MB total."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the message may still be sent on "
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
    """Send a message to an existing session.

    The session keeps its currently stored agent settings (model, effort,
    permission mode, ...). To change settings, use a future ``update-session``
    command (not implemented yet) — or the UI.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._session_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._session_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._session_request.discovery import (
        ServerDownError,
        check_heartbeat,
        get_data_dir,
    )
    from twicc.cli._session_request.drop_file import write_drop_file
    from twicc.cli._session_request.output import (
        emit_attachment_summary,
        emit_final,
        emit_progress,
        emit_validation_errors,
    )
    from twicc.cli._session_request.polling import poll_status
    from twicc.cli._session_request.prompt import resolve_prompt, PromptError
    from twicc.cli.send_message.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.providers.helpers import get_provider_helpers

    # ValidationError lives in the create_session sub-package; reused here
    # because the wire format expected by emit_validation_errors is the same
    # ``NamedTuple(field, code, message)`` tuple. No behavioral coupling —
    # only the shared error shape.
    from twicc.cli.create_session.validation import ValidationError

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # Local pre-check: session must exist, not be stale, and have a project
    # directory. The watcher-side service re-validates these in case the DB
    # state changed between this lookup and the actual send.
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

    # Resolve the prompt (inline text or file path → text content).
    try:
        text = resolve_prompt(prompt)
    except PromptError as e:
        emit_validation_errors(
            [ValidationError("PROMPT", "invalid_prompt", str(e))],
            json_output=json_output,
        )
        raise typer.Exit(1)

    emit_progress(f"✓ Prompt resolved ({len(text)} chars)", json_output=json_output)

    # Attachments are validated against the resolved session's provider, with
    # the resize cap derived from its currently stored ``selected_model``
    # (looked up via the helpers). The user has no way to override either
    # here — both come from the existing session.
    bootstrap = load_local_bootstrap()
    helpers_obj = get_provider_helpers(resolved.provider)
    support = (
        bootstrap.providers[resolved.provider].attachment_support
        if resolved.provider in bootstrap.providers else {}
    )

    # Effective model = stored on Session, with synced default as fallback,
    # then provider-specific consistency rules. Mirrors what the backend
    # service will compute (single source of truth: provider helpers).
    from twicc.core.models import Session as SessionModel
    session_row = SessionModel.objects.filter(id=resolved.session_id).first()
    from twicc.providers.helpers import AgentSettings
    stored = AgentSettings(**{
        field: getattr(session_row, field) for field in AgentSettings._fields
    })
    effective_settings = helpers_obj.resolve_agent_settings(stored)
    effective_settings = helpers_obj.enforce_agent_settings_consistency(effective_settings)
    effective_model = effective_settings.selected_model

    errors: list[ValidationError] = []
    try:
        attach_result = validate_and_encode(
            attach or [], support, helpers_obj, effective_model,
        )
    except AttachmentResizeError as e:
        errors.append(ValidationError(
            f"--attach {e.path}", "resize_failed", e.message,
        ))
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    for err in attach_result.errors:
        errors.append(ValidationError(f"--attach {err.file}", err.code, err.message))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    emit_progress(
        f"✓ Attachments validated "
        f"({len(attach_result.images)} images, "
        f"{len(attach_result.documents)} documents)",
        json_output=json_output,
    )
    emit_attachment_summary(attach_result.summary, json_output=json_output)

    # Payload — minimum required for the ``send`` kind. The watcher derives
    # provider, project, cwd, and current settings from the DB row.
    payload = {
        "session_id": resolved.session_id,
        "text": text,
        "images": attach_result.images,
        "documents": attach_result.documents,
    }

    drop = write_drop_file(get_data_dir(), payload, kind="send")
    emit_progress(
        f"→ Request submitted (request_uuid: {drop.request_uuid[:8]}...)",
        json_output=json_output,
    )

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    # Cleanup our own files.
    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        json_output=json_output,
        timeout=timeout,
    )

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
