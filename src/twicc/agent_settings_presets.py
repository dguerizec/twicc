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

import os
import tempfile

import orjson

from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers


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
    """Write the presets file for ``provider`` atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = _get_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(config, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
