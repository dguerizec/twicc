"""``twicc update-session <ID> settings`` sub-command.

Drops a ``kind="update_settings"`` payload in ``<data_dir>/sessions-pending/``
so the live TwiCC server applies the change via
:func:`twicc.core.services.session_update.update_session_settings_from_payload`.

The semantics mirror the UI's settings-only update path:
- Without ``--preset``, only the fields explicitly touched (per-flag values
  and ``--unset <field>``) are written.
- With ``--preset``, every settings field is rewritten: the preset's values
  set the fields it defines, fields absent from the preset become ``NULL``
  (use the synced default), per-flag values override the preset, and
  ``--unset`` forces a field back to ``NULL`` even if the preset set it.

``--model X`` combined with ``--unset model`` is rejected up-front
(``unset_conflict``). ``--unset <field>`` is also rejected when the
session's provider does not support the field (``unsupported_field``).
"""

from __future__ import annotations

import typer

from twicc.cli._session_request.help_context import load_help_context
from twicc.cli._session_request.help_strings import (
    context_max_help,
    default_suffix,
    model_help,
    parse_context_max,
    preset_help,
)


# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Same trick as
# ``create_session.command`` — kept Django-free, ~30 ms cold, degrades to
# "no info" on missing / malformed files.
_HELP_CTX = load_help_context()


# Public token (the user types after ``--unset``) -> AgentSettings field name.
# The token is the dash-cased form of the public flag name without the
# leading ``--``: ``--model`` becomes ``model``, ``--permission-mode`` becomes
# ``permission-mode``, etc.
_UNSET_TOKEN_TO_FIELD: dict[str, str] = {
    "model": "selected_model",
    "effort": "effort",
    "permission-mode": "permission_mode",
    "thinking": "thinking_enabled",
    "claude-in-chrome": "claude_in_chrome",
    "fast-mode": "fast_mode",
    "context-max": "context_max",
    "question-widget": "question_widget",
}


def _unset_help() -> str:
    tokens = sorted(_UNSET_TOKEN_TO_FIELD.keys())
    return (
        "Reset a setting back to NULL (use the synced default). Repeatable: "
        "pass once per setting to clear. Accepted tokens: "
        + ", ".join(f"'{t}'" for t in tokens)
        + ". Conflicts with the matching per-field flag (e.g. --unset model "
        "and --model X cannot be combined). Allowed alongside --preset to "
        "wipe a field the preset would otherwise have set."
    )


def update_settings_cmd(
    ctx: typer.Context,
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=preset_help(_HELP_CTX) + (
            " WARNING: passing --preset replaces every settings field. The "
            "preset's values write the fields it defines; fields absent from "
            "the preset become NULL (use the synced default); per-flag "
            "options override the preset; --unset still forces a field to "
            "NULL. Without --preset, only the fields you explicitly touch "
            "via per-flag options or --unset are written; every other field "
            "keeps its current value in DB."
        ),
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
            + default_suffix(_HELP_CTX, "effort")
        ),
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help=(
            "Tool permission policy. Claude Code: 'default', 'acceptEdits', 'plan', "
            "'dontAsk', 'bypassPermissions'. Codex: 'read_only', 'strict', 'auto', "
            "'autonomous', 'yolo'."
            + default_suffix(_HELP_CTX, "permission_mode")
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Enable extended thinking. Omit to leave the "
            "current value unchanged."
            + default_suffix(_HELP_CTX, "thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to "
            "leave the current value unchanged."
            + default_suffix(_HELP_CTX, "claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Claude Code Opus models only. Enable fast mode (higher token "
            "throughput, billed against extra usage credits). Omit to leave "
            "the current value unchanged."
            + default_suffix(_HELP_CTX, "fast_mode")
        ),
    ),
    question_widget: bool | None = typer.Option(
        None,
        "--question-widget/--no-question-widget",
        help=(
            "Enable interactive question widgets. Pass --no-question-widget "
            "to force the agent to ask its questions as plain text. Omit to "
            "leave the current value unchanged."
            + default_suffix(_HELP_CTX, "question_widget")
        ),
    ),
    context_max: str | None = typer.Option(
        None,
        "--context-max",
        help=context_max_help(_HELP_CTX),
    ),
    unset: list[str] = typer.Option(
        [],
        "--unset",
        help=_unset_help(),
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
    """Update the agent settings of an existing session.

    At least one of --preset, a per-field flag, or --unset is required. If
    nothing is passed, the command fails with ``no_op``.
    """
    session_id: str = ctx.obj

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._session_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._session_request.discovery import (
        ServerDownError, check_heartbeat, get_data_dir,
    )
    from twicc.cli._session_request.drop_file import write_drop_file
    from twicc.cli._session_request.output import (
        emit_final, emit_progress, emit_validation_errors,
    )
    from twicc.cli._session_request.polling import poll_status
    from twicc.cli._session_request.presets import (
        apply_preset_and_overrides, PresetError,
    )
    from twicc.cli._session_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._session_request.validation import (
        ValidationError,
        validate_hidden_constraints,
        validate_no_set_unset_conflict,
        validate_provider,
        validate_settings,
        validate_unset_fields,
    )
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

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

    bootstrap = load_local_bootstrap()
    provider = resolved.provider

    errors: list[ValidationError] = []

    # 1. Provider still enabled? (Race protection: the user may have disabled
    # it after the session was created.)
    errors.extend(validate_provider(provider, bootstrap))

    # 2. Parse ``--unset`` tokens to internal field names.
    unset_fields: list[str] = []
    for token in unset:
        field = _UNSET_TOKEN_TO_FIELD.get(token)
        if field is None:
            accepted = sorted(_UNSET_TOKEN_TO_FIELD.keys())
            errors.append(ValidationError(
                f"--unset {token}", "unknown_unset_field",
                f"Unknown setting {token!r}. Accepted tokens: {accepted}.",
            ))
        else:
            unset_fields.append(field)

    # 3. Parse ``--context-max``.
    context_max_int: int | None = None
    try:
        context_max_int = parse_context_max(context_max)
    except ValueError as e:
        errors.append(ValidationError("--context-max", "invalid_format", str(e)))

    overrides: dict[str, object | None] = {
        "selected_model": model,
        "effort": effort,
        "permission_mode": permission_mode,
        "thinking_enabled": thinking,
        "claude_in_chrome": claude_in_chrome,
        "fast_mode": fast_mode,
        "context_max": context_max_int,
        "question_widget": question_widget,
    }

    # 4. Contradictory ``--<field> VALUE`` + ``--unset <field>``.
    errors.extend(validate_no_set_unset_conflict(overrides, unset_fields))

    # 5. ``--unset <field>`` for fields the session's provider doesn't support.
    # Skipped if the provider isn't valid (would mask the real error).
    if provider in bootstrap.providers:
        errors.extend(validate_unset_fields(provider, unset_fields, bootstrap))

    # 6. No-op: nothing to update at all.
    # Use the raw user inputs (not the parsed / validated forms) so a
    # malformed value — ``--context-max 999bogus``, ``--unset bogus``,
    # ``--effort ultra``, ... — still counts as "the user tried to touch
    # this field" and doesn't spuriously trigger no_op on top of the parse
    # / validation error.
    user_touched_something = (
        preset is not None
        or any(v is not None for v in (
            model, effort, permission_mode, thinking, claude_in_chrome,
            fast_mode, context_max, question_widget,
        ))
        or bool(unset)
    )
    if not user_touched_something:
        errors.append(ValidationError(
            "<all>", "no_op",
            "Nothing to update; pass at least one of --preset, a per-field "
            "flag, or --unset <field>.",
        ))

    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    # 7. Resolve the final settings + updates dict.
    presets_for_provider = (
        bootstrap.providers[provider].presets
        if provider in bootstrap.providers else []
    )
    if preset is not None:
        try:
            settings = apply_preset_and_overrides(
                preset, presets_for_provider, overrides, unset=unset_fields,
            )
        except PresetError as e:
            emit_validation_errors(
                [ValidationError("--preset", "invalid_preset", str(e))],
                json_output=json_output,
            )
            raise typer.Exit(1)
        updates = settings._asdict()
        replace_all = True
    else:
        # Patch mode: only the explicitly-touched fields end up in the
        # payload. ``settings`` is built solely so ``validate_settings``
        # can vet the non-None values; it does not drive the DB write.
        settings = AgentSettings(**{f: overrides.get(f) for f in AgentSettings._fields})
        updates = {f: v for f, v in overrides.items() if v is not None}
        for field in unset_fields:
            updates[field] = None
        replace_all = False

    # 8. Validate each non-None setting against the provider's choices.
    errors = validate_settings(provider, settings, bootstrap)
    if errors:
        emit_validation_errors(errors, json_output=json_output)
        raise typer.Exit(1)

    # 9. Hidden invariants: if the target session is currently hidden, the
    # resulting settings must not break the non-interactive whitelist.
    # We must check the EFFECTIVE settings after the update, not the raw
    # user overrides (which have None for untouched fields). Build them
    # the same way the server does in session_update.py:
    #   1. Start from the current row's AgentSettings (baseline).
    #   2. Overlay ``updates`` (the actual dict that will be written, with
    #      None meaning "reset to synced default" for --unset fields).
    #   3. resolve + enforce_consistency.
    #   4. Validate.
    if resolved.hidden:
        merged_base = resolved.current_settings._asdict()
        merged_base.update(updates)
        merged_settings = AgentSettings(**merged_base)
        helpers_obj = get_provider_helpers(resolved.provider)
        effective_settings = helpers_obj.resolve_agent_settings(merged_settings)
        effective_settings = helpers_obj.enforce_agent_settings_consistency(effective_settings)
        hidden_errors = validate_hidden_constraints(
            resolved.provider, effective_settings, hidden=True,
        )
        if hidden_errors:
            emit_validation_errors(hidden_errors, json_output=json_output)
            raise typer.Exit(1)

    emit_progress(
        f"✓ Settings validated (replace_all={replace_all}, fields="
        f"{sorted(updates.keys())})",
        json_output=json_output,
    )

    payload = {
        "session_id": resolved.session_id,
        "updates": updates,
        "replace_all": replace_all,
    }

    drop = write_drop_file(get_data_dir(), payload, kind="update_settings")
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
