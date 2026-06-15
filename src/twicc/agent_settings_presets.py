"""Read/write agent settings presets, scoped per provider.

The on-disk format is identical for every provider — a single JSON object
``{"presets": [...]}`` whose entries are field/value dicts drawn from the
cross-provider :class:`AgentSettings` closed bundle. The only thing that
varies per provider is the file path, resolved via
:meth:`BaseProviderHelpers.get_settings_presets_path` which derives
``<data_dir>/<provider>-settings-presets.json`` from the provider key.

The wire surface is provider-agnostic too: the WS messages
``agent_settings_presets_updated`` (push) and
``update_agent_settings_presets`` (inbound) carry a ``provider`` field in
their payload — same pattern as ``usage_updated``.
"""

from __future__ import annotations

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers

# Sentinel preset name routing ``--preset`` to the effective defaults
# (resolved via ``resolve_agent_settings``) instead of a stored preset.
# Exposed as the first entry of ``twicc info presets`` and accepted by
# ``create-session`` / ``update-session settings`` to mean "reset the
# bundle to all-None so the back fills in the user's synced defaults".
DEFAULTS_PRESET_NAME = "__defaults__"

# Preset names reserved for synthetic / sentinel entries. Users cannot
# pick any of these for their own presets — they stay free for the
# matching feature.
RESERVED_PRESET_NAMES: frozenset[str] = frozenset({DEFAULTS_PRESET_NAME})


def _get_path(provider: Provider):
    return get_provider_helpers(provider).get_settings_presets_path()


def read_agent_settings_presets(provider: Provider) -> dict:
    """Read the presets file for ``provider``.

    Returns an empty config (``{"presets": []}``) when the file is
    missing or invalid JSON.
    """
    path = _get_path(provider)
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"presets": []}


def write_agent_settings_presets(provider: Provider, config: dict) -> None:
    """Write the presets file for ``provider`` atomically (whole-blob overwrite)."""
    atomic_write_json(_get_path(provider), config)
