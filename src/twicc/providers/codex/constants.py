"""Django-free constants for the Codex provider.

Mirrors :mod:`twicc.providers.claude_code.constants`. See that module for
the motivation (Django-free re-use by lightweight callers like
``twicc create-session --help``).
"""

from __future__ import annotations

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion


SYNCED_SETTINGS_DEFAULTS: dict = {
    "codexDefaultModel": "gpt",
    "codexDefaultEffort": "medium",
    "codexDefaultPermissionMode": "read_only",
    "codexDefaultContextMax": 272_000,
    "codexUsageReadFileEnabled": False,
    "codexUsageReadFilePath": "",
    "codexUsageDumpFileEnabled": False,
    "codexUsageDumpFilePath": "",
}


AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: [],
    AgentSettingCategory.IDLE: [
        "selected_model",
        "effort",
        "permission_mode",
        "context_max",
    ],
    AgentSettingCategory.STARTUP: [],
}


AGENT_SETTINGS_FIELDS_MAPPING: dict[str, str] = {
    "selected_model": "codexDefaultModel",
    "effort": "codexDefaultEffort",
    "permission_mode": "codexDefaultPermissionMode",
    "context_max": "codexDefaultContextMax",
}


# Codex CLI models the bundled binary accepts (verified at startup time
# via ``codex.models()``). ``selected_model_value`` returns the bare
# alias for ``latest=True`` entries (``"gpt"``, ``"gpt-mini"``) and the
# versioned alias for the rest (``"gpt-5.4"``), matching the Claude
# Code convention of bare-alias-for-latest / versioned-alias.
MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt",
        version="5.5",
        full_name="gpt-5.5",
        retirement_date=None,
        latest=True,
        provider_extra=None,
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt",
        version="5.4",
        full_name="gpt-5.4",
        retirement_date=None,
        latest=False,
        provider_extra=None,
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-mini",
        version="5.4",
        full_name="gpt-5.4-mini",
        retirement_date=None,
        latest=True,
        provider_extra=None,
    ),
]
