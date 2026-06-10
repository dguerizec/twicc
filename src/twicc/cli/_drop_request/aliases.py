"""Per-provider resolution of agent-settings keyword aliases (Django-free).

The CLI / skills let users pass semantic keywords (``max``, ``min``,
``strict``, ``open`` ...) wherever a concrete agent-settings value is expected.
Each provider declares its own ``{field: {alias: concrete_value}}`` table in
its ``constants.py`` (exposed on the local bootstrap as
``agent_settings_aliases``). This module turns a raw overrides dict into a
concrete, provider-specific one, so the rest of the pipeline — drop file,
server, DB — only ever sees normal literal values.

Three things happen here, all keyed to one resolved provider:

- **keyword resolution** for the ordered fields (``selected_model``,
  ``effort``, ``permission_mode``, ``context_max``). Resolution is
  **native-first**: a value the provider already accepts verbatim (e.g. effort
  ``max`` on Claude Code, ``strict`` on Codex) is never reinterpreted as a
  keyword — the alias table only fires when the value is NOT native. A
  genuinely unknown value on a supported field is left untouched so the
  downstream choice-validation rejects it (typo guard).
- **``context_max`` parsing**: its keyword resolves to a token-count string
  (``"1m"`` ...), then ``parse_context_max`` turns it into the int the rest of
  the pipeline expects. A malformed ``--context-max`` surfaces as a single
  ``invalid_format`` error here (it can only be checked once the provider — and
  thus its aliases — is known, so it is no longer validated provider-blind).
- **unsupported-field drop**: a field the provider does not support is dropped
  silently (set to ``None``), a no-op rather than an error. This is what lets a
  cross-provider batch (``--thinking`` over a mix of Claude Code and Codex
  sessions) apply each field wherever it makes sense without failing the
  sessions whose provider ignores it.

The functions take a ``ProviderBootstrap`` (duck-typed: anything exposing
``agent_settings_categories`` / ``agent_settings_choices`` /
``agent_settings_aliases`` / ``model_registry``), never a live model, so they
stay importable without ``django.setup()``.
"""

from __future__ import annotations

import sys

from twicc.cli._drop_request.help_strings import parse_context_max
from twicc.cli._drop_request.validation import ValidationError

# Ordered string fields that take keyword aliases at the string level.
# ``context_max`` is aliasable too but handled apart: its concrete form is an
# int, parsed after the string-level keyword swap.
_ALIASABLE_STRING_FIELDS = ("selected_model", "effort", "permission_mode")


def supported_fields(pb) -> set[str]:
    """Set of agent-settings fields this provider supports (union of its categories)."""
    out: set[str] = set()
    for fields in (pb.agent_settings_categories or {}).values():
        out.update(fields)
    return out


def _native_values(field: str, pb) -> set:
    """Values the provider accepts verbatim for ``field`` (drives native-first)."""
    if field == "selected_model":
        return {m.get("selected_model") for m in pb.model_registry or []}
    return set((pb.agent_settings_choices or {}).get(field, []))


def resolve_keyword(field: str, value, pb, *, untrusted: bool = False):
    """Native-first keyword resolution for one ordered string field.

    Returns the concrete value when ``value`` is a known keyword for this
    provider, the value unchanged otherwise (native value, or an unknown value
    left for choice-validation / the untrusted clamp to handle).

    When ``untrusted`` is set, a ``permission_mode`` keyword resolves against the
    provider's ``permission_mode_if_untrusted`` alias table instead — so ``max``
    lands on the most permissive *untrusted-allowed* mode (e.g. ``acceptEdits``),
    not ``bypassPermissions``. Native-first is unchanged: a concrete value still
    passes through, and an out-of-set one (``bypassPermissions``, ``open`` ...) is
    left for :func:`clamp_untrusted_permission_mode` to catch.
    """
    if value is None:
        return value
    if value in _native_values(field, pb):
        return value
    alias_field = (
        "permission_mode_if_untrusted"
        if untrusted and field == "permission_mode"
        else field
    )
    return (pb.agent_settings_aliases or {}).get(alias_field, {}).get(value, value)


def resolve_overrides(overrides: dict, pb, *, untrusted: bool = False) -> tuple[dict, list[ValidationError]]:
    """Resolve a raw overrides dict against one provider.

    Returns ``(resolved_overrides, errors)``. ``resolved_overrides`` keeps every
    key of ``overrides`` (so callers can rely on the shape) with keywords mapped
    to concrete values, ``context_max`` parsed to an int, and unsupported fields
    forced to ``None``. ``errors`` holds at most one ``invalid_format`` for a
    malformed ``--context-max``. Does not mutate the input — important for the
    batch, where the same overrides is reused across sessions of different
    providers.

    ``untrusted`` routes ``permission_mode`` keyword resolution through the
    untrusted alias table (see :func:`resolve_keyword`); the caller still applies
    :func:`clamp_untrusted_permission_mode` to the final bundle.
    """
    supported = supported_fields(pb)
    ctx_aliases = (pb.agent_settings_aliases or {}).get("context_max", {})
    resolved: dict = {}
    errors: list[ValidationError] = []
    for field, value in overrides.items():
        if value is None:
            resolved[field] = None
            continue
        if field not in supported:
            resolved[field] = None  # silent no-op for an unsupported field
            continue
        if field in _ALIASABLE_STRING_FIELDS:
            resolved[field] = resolve_keyword(field, value, pb, untrusted=untrusted)
        elif field == "context_max":
            # Keyword → token string (``"max"`` → ``"1m"``); a literal form
            # (``"200k"``, ``"272k"``, a plain int) is not a keyword and passes
            # through untouched, then both go through ``parse_context_max``.
            raw = ctx_aliases.get(value, value)
            try:
                resolved[field] = parse_context_max(raw)
            except ValueError as e:
                errors.append(ValidationError("--context-max", "invalid_format", str(e)))
        else:
            resolved[field] = value
    return resolved, errors


def clamp_untrusted_permission_mode(settings, pb, *, seed_when_absent: bool = False):
    """Clamp ``settings.permission_mode`` to the provider's untrusted-allowed set.

    Call only when the target project resolved untrusted. An in-set value passes
    through; an out-of-set one is replaced with
    ``pb.untrusted_permission_mode_default`` and a one-line note is printed to
    stderr. The replacement is exactly what the backend security floor would
    enforce at agent build; surfacing it here is the "clamp + clear message"
    instead of a silent server-side downgrade.

    A missing (``None``) permission_mode is left for the server by default. With
    ``seed_when_absent`` it is instead filled with the untrusted default —
    matching the frontend, which pre-fills the untrusted default for a draft in
    an untrusted project — so a CLI-created session stores the right value upfront
    rather than the trusted default re-clamped on every resume. No note is printed
    for the seed (it is the default, not a downgrade). Use it on session
    *creation*, never on an update patch (where a missing field means "leave it
    unchanged"). Providers without an untrusted set are a no-op.
    """
    modes = pb.untrusted_permission_modes
    if not modes:
        return settings
    mode = settings.permission_mode
    if mode is None:
        if seed_when_absent:
            return settings._replace(permission_mode=pb.untrusted_permission_mode_default)
        return settings
    if mode in modes:
        return settings
    clamped = pb.untrusted_permission_mode_default
    print(
        f"note: this project is untrusted — permission_mode {mode!r} is not "
        f"allowed there; using {clamped!r} instead (untrusted keywords: "
        f"min / safe / max).",
        file=sys.stderr,
    )
    return settings._replace(permission_mode=clamped)
