"""Static metadata always surfaced by ``twicc info``.

:data:`AVAILABLE_INFO_ARGUMENTS` — the positional section names the
command accepts, paired with a one-line description. Hand-written.
"""

from __future__ import annotations

AVAILABLE_INFO_ARGUMENTS: dict[str, str] = {
    "__description": (
        "Positional section names you can pass to 'info' to enrich the "
        "payload. Several can be composed in a single call; the output "
        "keys follow a canonical order regardless of input order."
    ),
    "presets": (
        "Agent settings presets per provider (with the synthetic "
        "'__defaults__' entry capturing effective defaults)."
    ),
    "commands": (
        "Slash / dollar commands per provider; filterable by --project "
        "and --filter."
    ),
    "models": (
        "Supported models per provider — identifiers, aliases, "
        "retirement dates, and capability flags."
    ),
    "agent-settings": (
        "Per-provider catalog of agent setting values and their model "
        "restrictions."
    ),
    "all": (
        "Shortcut expanding to presets + commands + models + "
        "agent-settings."
    ),
}
