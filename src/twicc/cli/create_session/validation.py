"""Pre-flight validation for the CLI ``create-session`` command.

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
