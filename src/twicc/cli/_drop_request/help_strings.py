"""Shared help-string builders for the CLI commands.

Each builder takes a :class:`HelpContext` snapshot (already loaded by the
calling command at module import time) and returns the rendered string for
one ``typer.Option(..., help=...)`` entry. Pure data formatting — no Django,
no I/O. Designed so ``create-session`` and ``update-session settings`` share
the same wording without duplicating any of the rendering logic.

The set of builders covers every flag whose help text depends on the user's
current providers / presets / defaults. Flags whose help is purely static
(``--timeout``, ``--attach``, ...) keep their inline help in the calling
command — duplicating a one-liner is cheaper than shipping a tiny helper
per flag.
"""

from __future__ import annotations

from twicc.cli._drop_request.help_context import HelpContext


_PROVIDER_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}


def provider_label(name: str) -> str:
    return _PROVIDER_LABELS.get(name, name)


def tokens_to_alias(value: int) -> str:
    """Compact token count alias: ``1_000_000 → "1m"``, ``200_000 → "200k"``.

    Non-integer or non-round inputs fall through to ``str(value)``.
    """
    if isinstance(value, int):
        if value % 1_000_000 == 0:
            return f"{value // 1_000_000}m"
        if value % 1_000 == 0:
            return f"{value // 1_000}k"
    return str(value)


def format_tokens(value) -> str:
    """Render a token count for ``--help`` strings: quoted compact alias."""
    return repr(tokens_to_alias(value))


def default_suffix(ctx: HelpContext, field: str, formatter=repr) -> str:
    """Render ``Current default: provider=value, ...`` for a field.

    Returns an empty string when no provider declares a default for the
    field (e.g. the field is provider-disabled or the data dir is empty).
    ``formatter`` re-formats each value (e.g. tokens rendered as 200k/1m).
    """
    per_provider = ctx.field_defaults.get(field)
    if not per_provider:
        return ""
    parts = [
        f"{_PROVIDER_LABELS[provider]}: {formatter(value)}"
        for provider, value in per_provider.items()
    ]
    return " Current default: " + " | ".join(parts) + "."


def provider_help(ctx: HelpContext) -> str:
    base = (
        "Provider to use: 'claude_code' or 'codex'. Falls back to the default "
        "provider from settings when omitted."
    )
    if ctx.providers:
        items = []
        for name in ctx.providers:
            if name == ctx.default_provider:
                items.append(f"'{name}' (default)")
            else:
                items.append(f"'{name}'")
        base += f" Currently enabled: {', '.join(items)}."
    return base


def preset_help(ctx: HelpContext) -> str:
    base = (
        "Name of a saved settings preset for the chosen provider. "
        "Per-flag options below override preset values; unset fields fall "
        "back to the defaults from settings."
    )
    if ctx.presets:
        lines = []
        for provider_name, names in ctx.presets.items():
            if names:
                lines.append(
                    f"{_PROVIDER_LABELS[provider_name]}: "
                    + ", ".join(f"'{name}'" for name in names)
                )
            else:
                lines.append(f"{_PROVIDER_LABELS[provider_name]}: (none)")
        base += " Currently saved: " + " | ".join(lines) + "."
    return base


def model_help(ctx: HelpContext) -> str:
    base = "Model alias (provider-specific)."
    chunks = []
    for provider_name, aliases in ctx.model_aliases.items():
        if not aliases:
            continue
        rendered = []
        for ma in aliases:
            if ma.is_latest:
                rendered.append(f"'{ma.alias}' (latest: {ma.version})")
            else:
                rendered.append(f"'{ma.alias}'")
        chunks.append(f"{provider_label(provider_name)}: {', '.join(rendered)}")
    if chunks:
        base += " " + ". ".join(chunks) + "."
    base += default_suffix(ctx, "selected_model")
    return base


def context_max_help(ctx: HelpContext) -> str:
    base = (
        "Max context window (accepted forms: '200k', '1m', '272k'). "
        "Claude Code: '200k' or '1m' (1m requires a 1m-capable model; "
        "otherwise silently capped to 200k) | Codex: '272k'."
    )
    base += default_suffix(ctx, "context_max", formatter=format_tokens)
    return base


def parse_context_max(value: str | None) -> int | None:
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
