"""Pre-flight validation for CLI commands that talk to the live TwiCC server.

Shared by ``twicc create-session`` and ``twicc update-session settings``.
Aggregates errors so the user sees every problem at once, lint-style.
"""

from __future__ import annotations

from typing import NamedTuple


_FIELD_TO_FLAG = {
    "selected_model": "--model",
    "thinking_enabled": "--thinking",
}


def _field_to_flag(field: str) -> str:
    if field in _FIELD_TO_FLAG:
        return _FIELD_TO_FLAG[field]
    return f"--{field.replace('_', '-')}"


def _field_to_unset_token(field: str) -> str:
    """Translate an internal AgentSettings field to its ``--unset <token>`` value.

    The token is the dash-cased form of the public flag name (without the
    leading ``--``). ``selected_model`` -> ``model``, ``thinking_enabled`` ->
    ``thinking``, etc. Mirrors :func:`_field_to_flag` but for the value the
    user actually types after ``--unset``.
    """
    flag = _field_to_flag(field)
    return flag.removeprefix("--")


class ValidationError(NamedTuple):
    field: str
    code: str
    message: str


def validate_provider(provider: str, bootstrap) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        errors.append(ValidationError(
            "provider", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        ))
        return errors
    if not bootstrap.disabled_providers_present:
        errors.append(ValidationError(
            "provider", "no_provider_configured",
            "TwiCC has never been started. Run `twicc` once to activate providers.",
        ))
        return errors
    if bootstrap.providers[provider].is_disabled:
        errors.append(ValidationError(
            "provider", "provider_disabled",
            f"Provider {provider} is disabled. Enable it from the UI or settings.",
        ))
    return errors


def validate_settings(provider: str, settings, bootstrap) -> list[ValidationError]:
    """Check each non-None field against the provider's choices."""
    errors: list[ValidationError] = []
    pb = bootstrap.providers[provider]
    categories = pb.agent_settings_categories or {}
    all_supported = set()
    for fields in categories.values():
        all_supported.update(fields)
    choices = pb.agent_settings_choices or {}
    model_ids = {m.get("selected_model") for m in pb.model_registry or []}

    for field, value in settings._asdict().items():
        if value is None:
            continue
        if field not in all_supported:
            errors.append(ValidationError(
                _field_to_flag(field), "unsupported_field",
                f"{field} is not supported by {provider}. Supported: {sorted(all_supported)}.",
            ))
            continue
        if field == "selected_model":
            if value not in model_ids:
                ids = sorted(m for m in model_ids if m)
                errors.append(ValidationError(
                    _field_to_flag(field), "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {ids}.",
                ))
        elif field in choices:
            if value not in choices[field]:
                errors.append(ValidationError(
                    _field_to_flag(field), "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {choices[field]}.",
                ))
    return errors


def validate_unset_fields(
    provider: str, unset_fields: list[str], bootstrap,
) -> list[ValidationError]:
    """Check each ``--unset <field>`` token against the provider's supported fields.

    Mirrors :func:`validate_settings` for the symmetric ``set`` case: a
    provider that does not support a setting cannot accept ``--unset`` for it
    either (the column on ``Session`` is provider-agnostic but ``null`` would
    silently match what the row already has — better to fail loudly).
    """
    errors: list[ValidationError] = []
    pb = bootstrap.providers[provider]
    categories = pb.agent_settings_categories or {}
    all_supported = set()
    for fields in categories.values():
        all_supported.update(fields)
    for field in unset_fields:
        if field not in all_supported:
            errors.append(ValidationError(
                f"--unset {_field_to_unset_token(field)}", "unsupported_field",
                f"{field} is not supported by {provider}. Supported: {sorted(all_supported)}.",
            ))
    return errors


def validate_no_set_unset_conflict(
    overrides: dict[str, object | None], unset_fields: list[str],
) -> list[ValidationError]:
    """Reject ``--<field> VALUE`` combined with ``--unset <field>`` for the same field.

    Setting a value and immediately unsetting it is a contradictory intent;
    we fail loudly rather than picking a winner silently.
    """
    errors: list[ValidationError] = []
    unset_set = set(unset_fields)
    for field, value in overrides.items():
        if value is not None and field in unset_set:
            errors.append(ValidationError(
                f"--unset {_field_to_unset_token(field)}", "unset_conflict",
                f"--unset {_field_to_unset_token(field)} conflicts with "
                f"{_field_to_flag(field)} {value!r}; pick one.",
            ))
    return errors
