"""Shared resolution of ``settings`` updates for the singular and batch CLI.

Single source of truth for ``twicc update-session <ID> settings`` and
``twicc update-sessions settings``: both parse the same flags and resolve the
same per-provider settings update. Split in two so the batch can reuse the
exact same logic with a different error-handling shape:

- :func:`parse_settings_flags` — **provider-independent** (global) validation of
  the raw CLI flags: unknown ``--unset`` token, malformed ``--context-max``,
  ``--<field>`` / ``--unset`` conflict, no-op. Same for every targeted session,
  so the singular and the batch validate it once.
- :func:`prepare_settings` — **per-provider** resolution against one resolved
  session: provider enabled, ``--unset`` field supported, preset resolution
  (presets are provider-scoped), value validation, hidden invariants. The
  singular runs it for one session; the batch runs it per id (a failure becomes
  a per-id ``validation_error`` while the other sessions proceed).

Both return either their successful product or a flat ``list[ValidationError]``
so the caller decides how to surface it (emit + exit for the singular / global
batch errors; a per-id entry for the batch).
"""

from __future__ import annotations

# Only Django-free modules at import time: this module is imported at the top of
# ``settings_command`` (loaded when the CLI boots, before ``django.setup()``),
# so the Django-heavy bits (``presets``, ``providers.helpers``) are imported
# lazily inside :func:`prepare_settings`. ``help_strings`` and ``validation``
# are Django-free at module top.
from twicc.cli._drop_request.help_strings import parse_context_max
from twicc.cli._drop_request.validation import (
    ValidationError,
    validate_no_set_unset_conflict,
    validate_provider,
    validate_settings,
    validate_unset_fields,
)


# Public token (typed after ``--unset``) -> AgentSettings field name. The token
# is the dash-cased public flag name without the leading ``--`` (``--model`` ->
# ``model``, ``--permission-mode`` -> ``permission-mode``, ...).
UNSET_TOKEN_TO_FIELD: dict[str, str] = {
    "model": "selected_model",
    "effort": "effort",
    "permission-mode": "permission_mode",
    "thinking": "thinking_enabled",
    "claude-in-chrome": "claude_in_chrome",
    "fast-mode": "fast_mode",
    "context-max": "context_max",
    "question-widget": "question_widget",
}


def unset_help() -> str:
    """Help string for the ``--unset`` option (shared by both commands)."""
    tokens = sorted(UNSET_TOKEN_TO_FIELD.keys())
    return (
        "Reset a setting back to NULL (use the synced default). Repeatable: "
        "pass once per setting to clear. Accepted tokens: "
        + ", ".join(f"'{t}'" for t in tokens)
        + ". Conflicts with the matching per-field flag (e.g. --unset model "
        "and --model X cannot be combined). Allowed alongside --preset to "
        "wipe a field the preset would otherwise have set."
    )


def parse_settings_flags(
    *,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    thinking: bool | None,
    claude_in_chrome: bool | None,
    fast_mode: bool | None,
    question_widget: bool | None,
    context_max: str | None,
    unset: list[str],
    preset: str | None,
) -> tuple[dict[str, object | None], list[str]] | list[ValidationError]:
    """Provider-independent parse + validation of the raw settings flags.

    Returns ``(overrides, unset_fields)`` on success, or a flat list of
    :class:`ValidationError` (``unknown_unset_field`` / ``invalid_format`` /
    ``unset_conflict`` / ``no_op``) when the flags are malformed on their own
    — independent of any target session's provider.
    """
    errors: list[ValidationError] = []

    # --unset tokens -> internal field names.
    unset_fields: list[str] = []
    for token in unset:
        field = UNSET_TOKEN_TO_FIELD.get(token)
        if field is None:
            accepted = sorted(UNSET_TOKEN_TO_FIELD.keys())
            errors.append(ValidationError(
                f"--unset {token}", "unknown_unset_field",
                f"Unknown setting {token!r}. Accepted tokens: {accepted}.",
            ))
        else:
            unset_fields.append(field)

    # --context-max parse.
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

    # Contradictory ``--<field> VALUE`` + ``--unset <field>``.
    errors.extend(validate_no_set_unset_conflict(overrides, unset_fields))

    # No-op: use the RAW user inputs (not parsed forms) so a malformed value
    # still counts as "the user tried to touch this field" and does not
    # spuriously add no_op on top of the parse error.
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
        return errors
    return overrides, unset_fields


def prepare_settings(
    resolved,
    *,
    overrides: dict[str, object | None],
    unset_fields: list[str],
    preset: str | None,
    bootstrap,
) -> tuple[dict[str, object | None], bool] | list[ValidationError]:
    """Resolve the settings update for one session against its provider.

    ``overrides`` / ``unset_fields`` come from :func:`parse_settings_flags`
    (already globally validated). Returns ``(updates, replace_all)`` ready for a
    ``kind="session:update_settings"`` payload, or a flat list of
    :class:`ValidationError` (``unknown_provider`` / ``provider_disabled`` /
    ``unsupported_field`` / ``invalid_preset`` / ``invalid_choice`` / hidden
    invariants) when this session's provider rejects the requested change.
    """
    # Lazy (Django-heavy) imports — see the module note above.
    from twicc.cli._drop_request.presets import PresetError, apply_preset_and_overrides
    from twicc.cli._drop_request.validation import validate_hidden_constraints
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    provider = resolved.provider

    # Provider still enabled + ``--unset`` fields supported by it.
    errors: list[ValidationError] = list(validate_provider(provider, bootstrap))
    if provider in bootstrap.providers:
        errors.extend(validate_unset_fields(provider, unset_fields, bootstrap))
    if errors:
        return errors

    # Resolve the final settings + updates dict.
    presets_for_provider = bootstrap.providers[provider].presets
    if preset is not None:
        try:
            settings = apply_preset_and_overrides(
                preset, presets_for_provider, overrides, unset=unset_fields,
            )
        except PresetError as e:
            return [ValidationError("--preset", "invalid_preset", str(e))]
        updates = settings._asdict()
        replace_all = True
    else:
        # Patch mode: only explicitly-touched fields end up in the payload.
        # ``settings`` is built solely so ``validate_settings`` can vet the
        # non-None values; it does not drive the DB write.
        settings = AgentSettings(**{f: overrides.get(f) for f in AgentSettings._fields})
        updates = {f: v for f, v in overrides.items() if v is not None}
        for field in unset_fields:
            updates[field] = None
        replace_all = False

    # Validate each non-None setting against the provider's choices.
    value_errors = validate_settings(provider, settings, bootstrap)
    if value_errors:
        return value_errors

    # Hidden invariants: if the session is currently hidden, the EFFECTIVE
    # settings after this update must keep the non-interactive whitelist.
    if resolved.hidden:
        merged_base = resolved.current_settings._asdict()
        merged_base.update(updates)
        merged_settings = AgentSettings(**merged_base)
        helpers_obj = get_provider_helpers(provider)
        effective = helpers_obj.resolve_agent_settings(merged_settings)
        effective = helpers_obj.enforce_agent_settings_consistency(effective)
        hidden_errors = validate_hidden_constraints(provider, effective, hidden=True)
        if hidden_errors:
            return hidden_errors

    return updates, replace_all
