"""Top-level ``twicc send-messages`` command — batch sibling of ``send-message``.

Sends the SAME message (and optional attachments) to several sessions at once,
selected with the same model as ``update-sessions``: a positional
``SESSION_ID...`` list merged (union) with ``--spawned-by`` / ``--descendants``
/ ``--annotation``, plus ``--siblings`` — unique to ``send-messages`` — which
lets a worker broadcast to its peers (the other sessions spawned by its parent).
Fans out one ``kind="session:send_message"`` drop per id via the shared
:func:`twicc.cli._batch_runner.run_batch` (no server-side change), polls every
status under one ``--timeout``, and emits an aggregated result.

The message text is resolved once (inline or a file path → its content), then
topped **per recipient** with the sender header (see
``_drop_request/sender_header.py``) when the caller is itself a TwiCC session —
the relation wording (spawned/parent/sibling/another) depends on each
recipient, so the delivered text can differ between them. The
attachments are validated/encoded **per session** against that session's
provider and effective model — so the same file can succeed on a Claude Code
session and be rejected on a Codex one (which only accepts images), surfacing as
a per-id ``validation_error`` while the other sessions still receive the
message.

Like the singular command this is asynchronous: a per-id ``"sent"`` only means
the message was handed to the agent, not that the agent finished. Follow up with
``twicc processes wait --spawned-by self <STATE>... --timeout N`` (skill:
``twicc-processes``) to await completion, then read results with ``twicc session
<ID> messages``.

``parent`` is NOT supported here (it is a singular, one-recipient concept); use
``send-message parent`` for that.
"""

from __future__ import annotations

import typer


def send_messages_cmd(
    session_ids: list[str] | None = typer.Argument(
        None,
        metavar="SESSION_ID...",
        help=(
            "Sessions to message. Optional if you pass --spawned-by or "
            "--descendants (explicit ids and scope-selected ids are merged, "
            "explicit first). Use 'self' for the current session."
        ),
    ),
    message: str = typer.Option(
        ...,
        "--message",
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
            "Path to a file to attach to every message (repeatable). Claude "
            "Code accepts PNG/JPEG/GIF/WebP/PDF/text/plain up to 5 MB each; "
            "Codex accepts images only. Max 100 files, 32 MB total. Validated "
            "per session against its provider — a file its provider rejects "
            "yields a per-id validation_error. Each value is a local file path "
            "OR a base64 data URI (data:<mime>;base64,<data>) for remote/API "
            "callers without a shared filesystem. Over --remote, prefix an "
            "absolute path with 'remote:' to read it on the remote server instead."
        ),
    ),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Also target sessions spawned by the given session_id, or 'self'. "
            "Merged (union) with explicit SESSION_IDs. 'parent' is not "
            "supported. Mutually exclusive with --descendants."
        ),
    ),
    descendants: str = typer.Option(
        None,
        "--descendants",
        help=(
            "Also target the proper descendants of the given session_id, or "
            "'self'. Merged (union) with explicit SESSION_IDs. 'parent' is not "
            "supported. Mutually exclusive with --spawned-by and --siblings."
        ),
    ),
    siblings: str = typer.Option(
        None,
        "--siblings",
        help=(
            "Also target the siblings of the given session_id — the other "
            "sessions spawned by the same parent — or 'self' (the canonical way "
            "for a worker to broadcast to its peers). The reference session "
            "itself is always excluded. Merged (union) with explicit "
            "SESSION_IDs. 'parent' is not supported. Mutually exclusive with "
            "--spawned-by and --descendants."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Narrow the --spawned-by / --descendants / --siblings scope by "
            "annotation. Repeatable, AND-combined. Requires a filiation scope; "
            "does NOT filter explicit SESSION_IDs. Same syntax as `twicc "
            "sessions --annotation`."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Wall-clock seconds to wait for the whole batch (drops are "
            "processed in parallel server-side, so this is a budget, not "
            'N×timeout). Per-id entries with no final status by the deadline '
            'get status="timeout". Must be > 0.'
        ),
    ),
) -> None:
    """Send the same message to several sessions at once.

    Selection, output shape, and exit codes match `update-sessions`: a positional
    SESSION_ID list merged (union) with `--spawned-by` / `--descendants` /
    `--annotation` (plus `--siblings`, unique to send-messages). Output is keyed
    by session id with a summary; a per-session failure never fails the batch
    (exit 0), exit 6 if no session was sent.

    When the caller is itself a TwiCC session, each recipient receives the
    text under a sender header (a single ":: message from <relation> session
    <id> (\"**<title>**\")" line, then the text) identifying the calling session;
    the relation wording (spawned/parent/sibling/another) is computed per
    recipient.

    Heads-up: each send starts/resumes an agent (real work, token spend); a
    batch can cold-start many stopped sessions at once.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._batch_runner import run_batch
    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.prompt import PromptError, resolve_prompt
    from twicc.cli._drop_request.sender_header import prefix_sender_header
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error
    from twicc.providers.helpers import get_provider_helpers

    # Resolve the message once — same text for every recipient (global, fatal).
    try:
        text = resolve_prompt(message)
    except PromptError as e:
        emit_error(f"Error: invalid --message: {e}", code=1)

    bootstrap = load_local_bootstrap()

    # Identify the calling agent once (PID ancestry; MCP sets a forced session
    # id) — the sender header itself is computed per recipient in ``_prepare``,
    # since the relation wording depends on each target. None for a human
    # invoking the CLI from a plain shell → no header.
    caller = resolve_current_session()

    def _prepare(resolved):
        """Per-id: build the send payload, encoding attachments for this provider."""
        recipient_text = prefix_sender_header(
            text,
            caller,
            recipient_id=resolved.session_id,
            recipient_spawned_by_id=resolved.spawned_by_id,
        )
        if not attach:
            return {
                "session_id": resolved.session_id,
                "text": recipient_text,
                "images": [],
                "documents": [],
            }

        # Attachments are provider/model-specific: validate + (re)encode against
        # THIS session's provider support and effective model. ``current_settings``
        # comes from the lookup, so no extra DB round-trip.
        helpers_obj = get_provider_helpers(resolved.provider)
        support = (
            bootstrap.providers[resolved.provider].attachment_support
            if resolved.provider in bootstrap.providers else {}
        )
        effective = helpers_obj.resolve_agent_settings(resolved.current_settings)
        effective = helpers_obj.enforce_agent_settings_consistency(effective)

        try:
            attach_result = validate_and_encode(
                attach, support, helpers_obj, effective.selected_model,
            )
        except AttachmentResizeError as e:
            return [ValidationError(f"--attach {e.path}", "resize_failed", e.message)]

        errors = [
            ValidationError(f"--attach {err.file}", err.code, err.message)
            for err in attach_result.errors
        ]
        if errors:
            return errors

        return {
            "session_id": resolved.session_id,
            "text": recipient_text,
            "images": attach_result.images,
            "documents": attach_result.documents,
        }

    run_batch(
        session_ids or [],
        kind="session:send_message",
        prepare=_prepare,
        timeout=timeout,
        success_status="sent",
        spawned_by=spawned_by,
        descendants=descendants,
        siblings=siblings,
        annotation=annotation,
    )
