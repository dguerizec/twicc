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


# Per-(field, value) human-readable description. Mirrors the
# ``description`` field of ``AGENT_SETTINGS_CHOICES`` in
# ``frontend/src/providers/codex/helpers.js`` — keep in sync. Surfaced
# by ``twicc info agent-settings``; absent values are silently omitted
# from the output.
AGENT_SETTINGS_DESCRIPTIONS: dict[str, dict] = {
    "permission_mode": {
        "read_only": "Read-only. Any write requires confirmation.",
        "strict": "Read-only. Writes are refused silently (no prompt).",
        "auto": "Writes freely in the project; asks to step outside.",
        "autonomous": "Like Auto but uninterrupted (sandbox protects).",
        "yolo": "No restrictions.",
    },
}


# Keyword aliases the CLI / skills accept in place of a concrete agent-settings
# value, resolved against Codex before the request leaves the client. See the
# matching table in ``twicc.providers.claude_code.constants`` for the full
# rationale (native-first resolution, token-count strings for ``context_max``).
# ``strict``, ``yolo`` and ``auto`` need no entry — they are already native
# Codex permission modes, so native-first keeps them as-is.
AGENT_SETTINGS_ALIASES: dict[str, dict[str, str]] = {
    "selected_model": {
        "min": "gpt-mini", "fastest": "gpt-mini", "cheapest": "gpt-mini",
        "max": "gpt", "strongest": "gpt",
    },
    "effort": {
        "min": "low", "max": "xhigh",
    },
    "context_max": {
        "min": "272k", "max": "272k",
    },
    "permission_mode": {
        "safe": "strict",
        "full": "yolo", "open": "yolo", "bypass": "yolo",
    },
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
