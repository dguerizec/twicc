"""Shared helpers for ``twicc info`` subcommands."""

from __future__ import annotations

import sys

import orjson


def emit_json(payload) -> None:
    """Print ``payload`` as indented JSON to stdout."""
    sys.stdout.buffer.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def resolve_providers(provider: str | None, include_disabled: bool = False):
    """Return ordered ``[(Provider, helpers)]`` to iterate over.

    Selection rules:

    - ``provider`` named → returns only that entry, whatever its enabled
      state. An explicit ask is honoured; an unknown key exits 1.
    - ``provider`` ``None`` → returns enabled providers only. Pass
      ``include_disabled=True`` to surface the disabled ones too. The
      ``info`` root command (which exists specifically to surface
      provider state) bypasses this helper and lists everything.
    """
    from twicc.providers.helpers import get_provider_helpers_registry

    pairs = get_provider_helpers_registry().items()
    if provider is not None:
        for prov, helpers in pairs:
            if prov.value == provider:
                return [(prov, helpers)]
        valid = ", ".join(p.value for p, _ in pairs)
        print(f"Error: unknown provider {provider!r}. Valid: {valid}.", file=sys.stderr)
        sys.exit(1)

    if include_disabled:
        return pairs

    from twicc.providers.state import is_provider_enabled

    return [(prov, helpers) for prov, helpers in pairs if is_provider_enabled(prov)]
