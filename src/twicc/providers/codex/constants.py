"""Django-free constants for the Codex provider.

Mirrors :mod:`twicc.providers.claude_code.constants`. See that module for
the motivation (Django-free re-use by lightweight callers like
``twicc create-session --help``).
"""

from __future__ import annotations

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion, assert_unique_weights


SYNCED_SETTINGS_DEFAULTS: dict = {
    "codexDefaultModel": "gpt",
    "codexDefaultEffort": "medium",
    "codexDefaultPermissionMode": "read_only",
    "codexDefaultUntrustedPermissionMode": "read_only",
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


# Permission modes a session may use in an UNTRUSTED (or unknown-trust) project:
# every mode that keeps at least one structural guardrail (permission prompt,
# read-only, or the workspace-write sandbox for ``auto``/``autonomous``). Only
# ``yolo`` — no guardrail at all — is excluded. The untrusted default seeds
# sessions created in untrusted projects and is the fallback the backend trust
# clamp applies to out-of-set values.
# See docs/plans/2026-06-09-project-trust-design.md §13.2.
UNTRUSTED_PERMISSION_MODES: frozenset[str] = frozenset({"read_only", "strict", "auto", "autonomous"})
UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: str = "codexDefaultUntrustedPermissionMode"


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


# Aliases the CLI / skills accept in place of a concrete agent-settings
# value, resolved against Codex before the request leaves the client. See the
# matching table in ``twicc.providers.claude_code.constants`` for the full
# rationale (native-first resolution, token-count strings for ``context_max``).
# ``strict``, ``yolo`` and ``auto`` need no entry — they are already native
# Codex permission modes, so native-first keeps them as-is.
AGENT_SETTINGS_ALIASES: dict[str, dict[str, str]] = {
    "selected_model": {
        "min": "gpt-mini", "fastest": "gpt-mini", "cheapest": "gpt-mini",
        "medium": "gpt-5.4", "balanced": "gpt-5.4",
        "max": "gpt", "strongest": "gpt",
    },
    "effort": {
        "min": "low", "max": "xhigh",
    },
    "context_max": {
        "min": "272k", "max": "272k",
    },
    "permission_mode": {
        "min": "strict", "safe": "strict",
        "max": "yolo", "full": "yolo", "open": "yolo", "bypass": "yolo",
    },
    # Untrusted projects use a restricted set (``yolo`` removed — see
    # ``UNTRUSTED_PERMISSION_MODES``). These aliases resolve only to values
    # inside that set, so ``min``/``safe``/``max`` stay meaningful when a session
    # is created in an untrusted project. ``max`` is the most permissive mode
    # still allowed there: ``autonomous`` (workspace-write sandbox, uninterrupted).
    "permission_mode_if_untrusted": {
        "min": "strict", "safe": "strict",
        "max": "autonomous",
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
        weight=100,
        provider_extra=None,
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt",
        version="5.4",
        full_name="gpt-5.4",
        retirement_date=None,
        latest=False,
        weight=90,
        provider_extra=None,
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-mini",
        version="5.4",
        full_name="gpt-5.4-mini",
        retirement_date=None,
        latest=True,
        weight=50,
        provider_extra=None,
    ),
]

assert_unique_weights(MODEL_VERSIONS)
