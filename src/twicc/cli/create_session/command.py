"""Top-level ``twicc create-session`` command (stub)."""

from __future__ import annotations

import typer

from twicc.cli.create_session.help_context import load_help_context

# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Cheap (~30 ms, pure file I/O,
# no Django) and degrades to "no extra info" on missing / malformed files.
_HELP_CTX = load_help_context()


_PROVIDER_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}


def _provider_label(name: str) -> str:
    return _PROVIDER_LABELS.get(name, name)


def _provider_help() -> str:
    base = (
        "Provider to use: 'claude_code' or 'codex'. Falls back to the default "
        "provider from settings when omitted."
    )
    if _HELP_CTX.providers:
        items = []
        for name in _HELP_CTX.providers:
            if name == _HELP_CTX.default_provider:
                items.append(f"'{name}' (default)")
            else:
                items.append(f"'{name}'")
        base += f" Currently enabled: {', '.join(items)}."
    return base


def _preset_help() -> str:
    base = (
        "Name of a saved settings preset for the chosen provider. "
        "Per-flag options below override preset values; unset fields fall "
        "back to the defaults from settings."
    )
    if _HELP_CTX.presets:
        lines = []
        for provider_name, names in _HELP_CTX.presets.items():
            if names:
                lines.append(f"{_PROVIDER_LABELS[provider_name]}: {', '.join(f"'{name}'" for name in names)}")
            else:
                lines.append(f"{_PROVIDER_LABELS[provider_name]}: (none)")
        base += " Currently saved: " + " | ".join(lines) + "."
    return base


def _format_tokens(value) -> str:
    """Render a token count compactly: 1_000_000 → 1m, 200_000 → 200k."""
    if isinstance(value, int):
        if value % 1_000_000 == 0:
            value = f"{value // 1_000_000}m"
        elif value % 1_000 == 0:
            value = f"{value // 1_000}k"
    return repr(str(value))


def _default_suffix(field: str, formatter=repr) -> str:
    """Render ``Current default: provider=value, ...`` for a field.

    Returns an empty string when no provider declares a default for the
    field (e.g. the field is provider-disabled or the data dir is empty).
    ``formatter`` re-formats each value (e.g. tokens rendered as 200k/1m).
    """
    per_provider = _HELP_CTX.field_defaults.get(field)
    if not per_provider:
        return ""
    parts = [
        f"{_PROVIDER_LABELS[provider]}: {formatter(value)}"
        for provider, value in per_provider.items()
    ]
    return " Current default: " + " | ".join(parts) + "."


def _model_help() -> str:
    base = "Model alias (provider-specific)."
    chunks = []
    for provider_name, aliases in _HELP_CTX.model_aliases.items():
        if not aliases:
            continue
        rendered = []
        for ma in aliases:
            if ma.is_latest:
                rendered.append(f"'{ma.alias}' (latest: {ma.version})")
            else:
                rendered.append(f"'{ma.alias}'")
        chunks.append(f"{_provider_label(provider_name)}: {', '.join(rendered)}")
    if chunks:
        base += " " + ". ".join(chunks) + "."
    base += _default_suffix("selected_model")
    return base


def _context_max_help() -> str:
    base = (
        "Max context window (accepted forms: '200k', '1m', '272k'). "
        "Claude Code: '200k' or '1m' (1m requires a 1m-capable model; "
        "otherwise silently capped to 200k) | Codex: '272k'."
    )
    base += _default_suffix("context_max", formatter=_format_tokens)
    return base


def _parse_context_max(value: str | None) -> int | None:
    """Parse ``--context-max`` accepting ``1m``/``200k``/``272k``/plain int."""
    if value is None:
        return None
    s = value.strip().lower()
    if not s:
        return None
    multiplier = 1
    if s.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    try:
        n = int(s)
    except ValueError:
        raise ValueError(
            f"invalid --context-max {value!r}; expected forms like 200k, 1m, "
            f"or a plain integer."
        )
    return n * multiplier


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
        help=_provider_help(),
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=_preset_help(),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=_model_help(),
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help=(
            "Reasoning effort. Claude Code: 'low', 'medium', 'high', 'xhigh', 'max' "
            "(xhigh/max require a capable model; otherwise silently demoted). "
            "Codex: 'low', 'medium', 'high', 'xhigh'."
            + _default_suffix("effort")
        ),
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help=(
            "Tool permission policy. Claude Code: 'default', 'acceptEdits', 'plan', "
            "'dontAsk', 'bypassPermissions'. Codex: 'read_only', 'strict', 'auto', "
            "'autonomous', 'yolo'."
            + _default_suffix("permission_mode")
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Enable extended thinking. Omit to keep the "
            "preset's value (or the default from settings)."
            + _default_suffix("thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to "
            "keep the preset's value (or the default from settings)."
            + _default_suffix("claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Claude Code Opus models only. Enable fast mode (higher token throughput, "
            "billed against extra usage credits). Omit to keep the preset's value "
            "(or the default from settings)."
            + _default_suffix("fast_mode")
        ),
    ),
    context_max: str | None = typer.Option(
        None,
        "--context-max",
        help=_context_max_help(),
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
            "images only. Max 100 files, 32 MB total."
        ),
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
    """Create a new session by dropping a request file the server picks up.

    All options are optional — only the PROMPT argument is required. With no
    flags, the command uses the default provider from settings, falls back
    to the current directory as the project, and lets the defaults from
    settings drive model / effort / permission mode / etc.
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
    from twicc.cli._session_request.discovery import ServerDownError, check_heartbeat, get_data_dir
    from twicc.cli._session_request.drop_file import write_drop_file
    from twicc.cli._session_request.output import (
        emit_attachment_summary,
        emit_final,
        emit_progress,
        emit_validation_errors,
    )
    from twicc.cli._session_request.polling import poll_status
    from twicc.cli._session_request.prompt import resolve_prompt, PromptError
    from twicc.cli.create_session.project import resolve_project, ProjectError
    from twicc.cli.create_session.presets import apply_preset_and_overrides, PresetError
    from twicc.cli.create_session.validation import (
        ValidationError,
        validate_provider,
        validate_settings,
    )
    from twicc.providers.helpers import get_provider_helpers

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    bootstrap = load_local_bootstrap()
    emit_progress(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)
    emit_progress(
        f"✓ Bootstrap loaded ({len(bootstrap.providers)} providers, "
        f"{sum(len(p.presets) for p in bootstrap.providers.values())} presets total)",
        json_output=json_output,
    )

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
                json_output=json_output,
            )
            raise typer.Exit(1)
        emit_progress(
            f"✓ Provider (from default provider in settings): {provider}",
            json_output=json_output,
        )

    # Parse --context-max early so other validation can see the int form.
    try:
        context_max_int = _parse_context_max(context_max)
    except ValueError as e:
        emit_validation_errors(
            [ValidationError("--context-max", "invalid_format", str(e))],
            json_output=json_output,
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
        }
        preset_list = bootstrap.providers[provider].presets if provider in bootstrap.providers else []
        settings = apply_preset_and_overrides(preset, preset_list, overrides)
    except PromptError as e:
        emit_validation_errors([ValidationError("prompt", "invalid_prompt", str(e))], json_output=json_output)
        raise typer.Exit(1)
    except ProjectError as e:
        emit_validation_errors([ValidationError("--project", "invalid_project", str(e))], json_output=json_output)
        raise typer.Exit(1)
    except PresetError as e:
        emit_validation_errors([ValidationError("--preset", "invalid_preset", str(e))], json_output=json_output)
        raise typer.Exit(1)

    emit_progress(f"✓ Prompt resolved ({len(text)} chars)", json_output=json_output)
    emit_progress(
        f"✓ Project {resolved_project.project_id!r} "
        f"({'existing' if resolved_project.existed else 'new'})",
        json_output=json_output,
    )

    errors: list[ValidationError] = []
    errors.extend(validate_provider(provider, bootstrap))
    if not errors:  # only validate settings if the provider is OK
        errors.extend(validate_settings(provider, settings, bootstrap))

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

    support = bootstrap.providers[provider].attachment_support if provider in bootstrap.providers else {}
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

    emit_progress("✓ Settings validated", json_output=json_output)
    emit_progress(
        f"✓ Attachments validated "
        f"({len(attach_result.images)} images, "
        f"{len(attach_result.documents)} documents)",
        json_output=json_output,
    )
    emit_attachment_summary(attach_result.summary, json_output=json_output)

    # Build the WS-compatible payload. ``directory`` is passed alongside
    # ``project_id`` so the server (PendingSessionsWatcher → service) can
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
        **settings._asdict(),
    }

    drop = write_drop_file(get_data_dir(), payload)
    emit_progress(
        f"→ Request submitted (request_uuid: {drop.request_uuid[:8]}...)",
        json_output=json_output,
    )

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    # Cleanup our own files (cf. spec §5.5).
    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    emit_final(
        outcome,
        request_uuid=drop.request_uuid,
        json_output=json_output,
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
