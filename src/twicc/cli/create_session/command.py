"""Top-level ``twicc create-session`` command (stub)."""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_context import load_help_context
from twicc.cli._drop_request.help_strings import (
    context_max_help,
    default_suffix,
    model_help,
    parse_context_max,
    preset_help,
    provider_help,
)

# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Cheap (~30 ms, pure file I/O,
# no Django) and degrades to "no extra info" on missing / malformed files.
_HELP_CTX = load_help_context()


def create_session_cmd(
    prompt: str = typer.Argument(
        ...,
        help="Prompt text, or path to a file whose content is the prompt.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help=(
            "Project id (with or without leading dash) or directory path "
            "(absolute or relative). New directories are auto-resolved to "
            "their canonical project id via realpath. Defaults to the current "
            "working directory."
        ),
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help=provider_help(_HELP_CTX),
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=preset_help(_HELP_CTX),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=model_help(_HELP_CTX),
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help=(
            "Reasoning effort. Claude Code: 'low', 'medium', 'high', 'xhigh', 'max' "
            "(xhigh/max require a capable model; otherwise silently demoted). "
            "Codex: 'low', 'medium', 'high', 'xhigh'."
            + default_suffix(_HELP_CTX,"effort")
        ),
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help=(
            "Tool permission policy. Claude Code: 'default', 'acceptEdits', 'plan', "
            "'dontAsk', 'bypassPermissions'. Codex: 'read_only', 'strict', 'auto', "
            "'autonomous', 'yolo'."
            + default_suffix(_HELP_CTX,"permission_mode")
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Enable extended thinking. Omit to keep the "
            "preset's value (or the default from settings)."
            + default_suffix(_HELP_CTX,"thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to "
            "keep the preset's value (or the default from settings)."
            + default_suffix(_HELP_CTX,"claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Claude Code Opus models only. Enable fast mode (higher token throughput, "
            "billed against extra usage credits). Omit to keep the preset's value "
            "(or the default from settings)."
            + default_suffix(_HELP_CTX,"fast_mode")
        ),
    ),
    question_widget: bool | None = typer.Option(
        None,
        "--question-widget/--no-question-widget",
        help=(
            "Enable interactive question widgets. Pass --no-question-widget to "
            "force the agent to ask its questions as plain text instead of using "
            "a UI widget (useful for CLI workflows where you want to read and "
            "answer questions textually, without going through the TwiCC UI). "
            "Honored by providers that map this flag to a widget tool; currently "
            "Claude Code (maps to AskUserQuestion). Omit to use the default "
            "(widget enabled)."
            + default_suffix(_HELP_CTX,"question_widget")
        ),
    ),
    hidden: bool = typer.Option(
        False,
        "--hidden",
        help=(
            "Create the session as hidden — invisible from every list, "
            "search, broadcast, and counter shown to the user, while still "
            "counted in cost aggregates. Requires a non-interactive "
            "permission_mode (bypassPermissions/dontAsk for Claude Code; "
            "yolo/strict for Codex) and question_widget=False."
        ),
    ),
    context_max: str | None = typer.Option(
        None,
        "--context-max",
        help=context_max_help(_HELP_CTX),
    ),
    title: str | None = typer.Option(
        None,
        "--title",
        help=(
            "Custom session title (max 200 characters). When omitted, the "
            "title is auto-derived from the first message."
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
            "callers attach files without a shared filesystem."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Free-form session annotation as key=value (repeatable). Keys may "
            "use dotted paths; values support true, false, null, numbers, or strings."
        ),
    ),
    annotations_file: str | None = typer.Option(
        None,
        "--annotations-file",
        help="Path to a JSON object containing session annotations.",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the session may still be created on "
            "the server side."
        ),
    ),
) -> None:
    """Create a new session by dropping a request file the server picks up.

    All options are optional — only the PROMPT argument is required. With no
    flags, the command uses the default provider from settings, falls back
    to the current directory as the project, and lets the defaults from
    settings drive model / effort / permission mode / etc.

    Asynchronous: a "created" status only means the session was started and
    the prompt handed to the agent — not that the agent has finished, which
    can take a while. It keeps working in the background. To block until it
    reaches a given state, follow up with
    "twicc process <SESSION_ID> wait <STATE>... --timeout N" (e.g. user_turn
    once the reply is complete).
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.annotations import parse_annotations
    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.prompt import resolve_prompt, PromptError
    from twicc.cli._drop_request.project import resolve_project
    from twicc.cli._drop_request.presets import apply_preset_and_overrides, PresetError
    from twicc.cli._drop_request.validation import (
        ValidationError,
        validate_hidden_constraints,
        validate_provider,
        validate_settings,
    )
    from twicc.cli._output import emit_error
    from twicc.providers.helpers import get_provider_helpers

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    bootstrap = load_local_bootstrap()

    # Resolve provider: explicit flag wins; otherwise fall back to the
    # default provider from settings. If neither is available, fail fast
    # with a clear validation error.
    if provider is None:
        provider = bootstrap.default_provider
        if provider is None:
            emit_validation_errors(
                [ValidationError(
                    "--provider",
                    "no_default_provider",
                    "No --provider given and no default provider set in settings.",
                )],
            )
            raise typer.Exit(1)

    # Parse --context-max early so other validation can see the int form.
    try:
        context_max_int = parse_context_max(context_max)
    except ValueError as e:
        emit_validation_errors(
            [ValidationError("--context-max", "invalid_format", str(e))],
        )
        raise typer.Exit(1)

    try:
        text = resolve_prompt(prompt)
        resolved_project = resolve_project(project)
        overrides = {
            "selected_model": model,
            "effort": effort,
            "permission_mode": permission_mode,
            "thinking_enabled": thinking,
            "claude_in_chrome": claude_in_chrome,
            "fast_mode": fast_mode,
            "context_max": context_max_int,
            "question_widget": question_widget,
        }
        preset_list = bootstrap.providers[provider].presets if provider in bootstrap.providers else []
        settings = apply_preset_and_overrides(preset, preset_list, overrides)
    except PromptError as e:
        emit_validation_errors([ValidationError("prompt", "invalid_prompt", str(e))])
        raise typer.Exit(1)
    except PresetError as e:
        emit_validation_errors([ValidationError("--preset", "invalid_preset", str(e))])
        raise typer.Exit(1)

    # ``resolve_project`` only raises in API mode (the relative-path guard,
    # via ``emit_error``); otherwise, when the input is an id with no matching
    # Project row and no on-disk directory backing it, it returns
    # ``directory=None``. ``create-session`` needs the directory (to seed
    # the project server-side), so reject here with the same UX as before.
    if resolved_project.directory is None:
        emit_validation_errors(
            [ValidationError(
                "--project", "invalid_project",
                f"--project: {project!r} is neither an existing directory "
                f"nor a known project_id (tried also with leading '-').",
            )],
        )
        raise typer.Exit(1)

    errors: list[ValidationError] = []
    annotations, annotation_errors = parse_annotations(annotation or [], annotations_file)
    errors.extend(annotation_errors)
    provider_errors = validate_provider(provider, bootstrap)
    errors.extend(provider_errors)
    if not provider_errors:  # only validate settings if the provider is OK
        errors.extend(validate_settings(provider, settings, bootstrap))

    # --hidden auto-forces question_widget=False so the agent never lands
    # in an interactive state nobody can see. Only error when the user
    # explicitly contradicts that with --question-widget — the validator
    # below catches that case once the effective bundle is built.
    if hidden and settings.question_widget is not True:
        settings = settings._replace(question_widget=False)

    # Resolve effective settings (None → synced default, then consistency
    # demotion) so we know the real model that will drive the resize cap.
    # The back-end service redoes this for the actual session creation;
    # the duplicated call here is cheap and local.
    helpers_obj = (
        get_provider_helpers(provider) if provider in bootstrap.providers else None
    )
    effective_model: str | None = None
    if helpers_obj is not None and not errors:
        effective_settings = helpers_obj.resolve_agent_settings(settings)
        effective_settings = helpers_obj.enforce_agent_settings_consistency(
            effective_settings
        )
        effective_model = effective_settings.selected_model
        errors.extend(validate_hidden_constraints(provider, effective_settings, hidden=hidden))

    support = bootstrap.providers[provider].attachment_support if provider in bootstrap.providers else {}
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

    # Auto-fill spawned_by silently via PID ancestry. No CLI flag exposes
    # this — the agent never has to know its own session_id to call us.
    try:
        from twicc.cli._drop_request.whoami import resolve_current_session
        current = resolve_current_session()
        spawned_by_session_id = current.id if current is not None else None
    except Exception:
        spawned_by_session_id = None

    # Build the WS-compatible payload. ``directory`` is passed alongside
    # ``project_id`` so the server (DropRequestsWatcher → service) can
    # auto-create the Project from inside the main process — that's where
    # the WS broadcasts of ``project_added`` and ``workspaces_updated``
    # need to originate to reach connected UI clients live.
    payload = {
        "project_id": resolved_project.project_id,
        "directory": resolved_project.directory,
        "provider": provider,
        "text": text,
        "title": title,
        "images": attach_result.images,
        "documents": attach_result.documents,
        "hidden": hidden,
        "spawned_by_session_id": spawned_by_session_id,
        "annotations": annotations,
        **settings._asdict(),
    }

    drop = write_drop_file(payload, kind="session:create")

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    # Cleanup our own files (cf. spec §5.5).
    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        timeout=timeout,
    )

    # Exit code mapping (spec §2.5)
    if outcome.status == "created":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
