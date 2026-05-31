"""``twicc info presets`` — list per-provider agent settings presets."""

from __future__ import annotations


def build(provider: str | None, include_disabled: bool = False) -> dict[str, list[dict]]:
    """Return ``{provider: [presets]}`` (no JSON emission, no Django setup).

    Each provider's list starts with a synthetic ``__defaults__`` entry
    carrying the values that :meth:`resolve_agent_settings` would return
    for an all-None bundle — i.e. the user's synced overrides (or the
    hard-coded fallback when nothing is set). Followed by the user
    presets in their on-disk order.

    Caller must have already initialised Django.
    """
    from twicc.agent_settings_presets import read_agent_settings_presets
    from twicc.cli._drop_request.presets import PRESET_KEY_MAP
    from twicc.cli.info._common import resolve_providers
    from twicc.providers.helpers import AGENT_SETTINGS_HIDDEN_FROM_FRONTEND, AgentSettings

    # Inverse of PRESET_KEY_MAP: translate AgentSettings field names back
    # to the historical preset keys (``model`` / ``thinking``).
    wire_to_preset = {v: k for k, v in PRESET_KEY_MAP.items()}

    def _defaults_entry(helpers) -> dict:
        resolved = helpers.resolve_agent_settings(AgentSettings())._asdict()
        out: dict = {"name": "__defaults__"}
        for field, value in resolved.items():
            if field in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND:
                continue
            key = wire_to_preset.get(field, field)
            out[key] = value
        return out

    output: dict[str, list[dict]] = {}
    for prov, helpers in resolve_providers(provider, include_disabled=include_disabled):
        entries: list[dict] = [_defaults_entry(helpers)]
        config = read_agent_settings_presets(prov)
        for preset in config.get("presets", []):
            if isinstance(preset, dict):
                entries.append(preset)
        output[prov.value] = entries

    return output
