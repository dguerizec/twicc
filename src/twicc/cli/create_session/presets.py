"""Resolve ``--preset`` lookup and the merge with CLI overrides.

The preset file uses the historical keys ``model`` and ``thinking`` which
map to ``selected_model`` and ``thinking_enabled`` on ``AgentSettings``.
"""

from __future__ import annotations

from twicc.providers.helpers import AgentSettings

PRESET_KEY_MAP = {
    "model": "selected_model",
    "thinking": "thinking_enabled",
}


class PresetError(Exception):
    pass


def find_preset(presets: list[dict], name: str) -> dict | None:
    for p in presets:
        if p.get("name") == name:
            return p
    return None


def apply_preset_and_overrides(
    preset_name: str | None,
    presets: list[dict],
    overrides: dict[str, object | None],
) -> AgentSettings:
    """Build the final ``AgentSettings`` from preset + per-flag overrides.

    Order:
      1. Start with all-None.
      2. If a preset is named, merge its values (after key remapping).
      3. Each non-None override replaces the corresponding field.

    A field that is neither in the preset nor in the overrides stays
    ``None`` and the back will fall back to the synced default.
    """
    fields = {name: None for name in AgentSettings._fields}

    if preset_name is not None:
        preset = find_preset(presets, preset_name)
        if preset is None:
            names = ", ".join(p.get("name", "<unnamed>") for p in presets) or "<empty>"
            raise PresetError(
                f"Preset {preset_name!r} not found. Available: {names}"
            )
        for raw_key, raw_value in preset.items():
            if raw_key == "name":
                continue
            field = PRESET_KEY_MAP.get(raw_key, raw_key)
            if field in fields:
                fields[field] = raw_value

    for field, value in overrides.items():
        if value is None:
            continue
        if field in fields:
            fields[field] = value

    return AgentSettings(**fields)
