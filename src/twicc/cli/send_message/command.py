"""Top-level ``twicc send-message`` command."""

from __future__ import annotations

import typer


def send_message_cmd(
    session_id: str = typer.Argument(
        ...,
        help=(
            "Id of the existing session to send the message to, or the "
            "keyword 'parent' to target the parent of the calling session "
            "(resolved via PID ancestry → current session's spawned_by)."
        ),
    ),
    prompt: str = typer.Argument(
        ...,
        help=(
            "Message text, or path to a file whose content is the message. Over "
            "--remote the file is read locally; prefix an absolute path with "
            "'remote:' to read it on the remote server instead."
        ),
    ),
    attach: list[str] = typer.Option(
        [],
        "--attach",
        help=(
            "Path to a file to attach (repeatable). Claude Code accepts "
            "PNG/JPEG/GIF/WebP/PDF/text/plain up to 5 MB each. Codex accepts "
            "images only. Max 100 files, 32 MB total. "
            "Each value is either a local file path OR a base64 data URI "
            "(data:<mime>;base64,<data>) — the data-URI form lets remote/API "
            "callers attach files without a shared filesystem. Over --remote, "
            "prefix an absolute path with 'remote:' to read it on the remote "
            "server instead."
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
) -> None:
    """Send a message to an existing session.

    The session keeps its currently stored agent settings (model, effort,
    permission mode, ...). To change settings, use
    ``twicc update-session <ID> settings`` — or the UI.

    Asynchronous: a "sent" status only means the message was handed to the
    agent — not that the agent has finished processing it. To block until it
    reaches a given state, follow up with
    "twicc process <SESSION_ID> wait <STATE>... --timeout N". To wait for the
    end of the turn this message triggers, add --transition so it does not
    match the idle user_turn the session was already in before the message:
    "wait user_turn --transition --timeout N".
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import (
        ServerDownError,
        check_heartbeat,
    )
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import (
        emit_final,
        emit_validation_errors,
    )
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.prompt import resolve_prompt, PromptError
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error
    from twicc.providers.helpers import get_provider_helpers

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # 'parent' keyword: PID ancestry → current TwiCC session (same mechanism
    # as `--spawned-by self` on create-session) → its `spawned_by` field, which
    # is the session that originally created the current one via
    # `twicc create-session`. Done before lookup_session, which expects a real
    # session_id. The current session's id is kept aside so the message can be
    # prefixed with the caller's identity (the recipient parent is otherwise
    # blind to which spawned session is talking).
    parent_caller_id: str | None = None
    if session_id == "parent":
        current_session = resolve_current_session()
        if current_session is None:
            emit_validation_errors(
                [ValidationError(
                    "SESSION_ID",
                    "parent_not_found",
                    "No TwiCC session found in the process ancestry. "
                    "Run from inside a TwiCC-spawned agent, or pass an "
                    "explicit session_id.",
                )],
            )
            raise typer.Exit(1)
        if current_session.spawned_by_id is None:
            emit_validation_errors(
                [ValidationError(
                    "SESSION_ID",
                    "parent_not_found",
                    f"Current session {current_session.id!r} has no "
                    f"spawned_by — it was not created via "
                    f"`twicc create-session` from a parent agent. Pass an "
                    f"explicit session_id instead.",
                )],
            )
            raise typer.Exit(1)
        parent_caller_id = current_session.id
        session_id = current_session.spawned_by_id

    # Local pre-check: session must exist, not be stale, and have a project
    # directory. The watcher-side service re-validates these in case the DB
    # state changed between this lookup and the actual send.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors(
            [ValidationError("SESSION_ID", e.code, e.message)],
        )
        raise typer.Exit(1)

    # Resolve the prompt (inline text or file path → text content).
    try:
        text = resolve_prompt(prompt)
    except PromptError as e:
        emit_validation_errors(
            [ValidationError("PROMPT", "invalid_prompt", str(e))],
        )
        raise typer.Exit(1)

    # When the 'parent' keyword was used, prefix the message so the recipient
    # parent session sees who is talking — otherwise it would receive an
    # anonymous follow-up from "the user" and have no way to tell which of
    # its spawned sessions sent it.
    if parent_caller_id is not None:
        text = (
            f"> Message from your spawned session {parent_caller_id}\n"
            f"---\n"
            f"{text}"
        )

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
        emit_validation_errors(errors)
        raise typer.Exit(1)

    for err in attach_result.errors:
        errors.append(ValidationError(f"--attach {err.file}", err.code, err.message))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Payload — minimum required for the ``send`` kind. The watcher derives
    # provider, project, cwd, and current settings from the DB row.
    payload = {
        "session_id": resolved.session_id,
        "text": text,
        "images": attach_result.images,
        "documents": attach_result.documents,
    }

    drop = write_drop_file(payload, kind="session:send_message")

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    # Cleanup our own files.
    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        timeout=timeout,
    )

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
