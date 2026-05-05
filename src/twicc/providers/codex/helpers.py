"""
Codex implementation of :class:`BaseProviderHelpers`.

Minimal first cut: Codex is registered so it can be selected as the default
provider and have its agent settings (model, effort, permission_mode,
context_max) edited from the popover and presets dialog. There is no
agent runtime yet — the front gates ``canSendMessage`` to ``False`` for
this provider, so no Codex session can actually be created.
"""

from __future__ import annotations

from typing import ClassVar

from twicc.core.enums import Provider
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    ModelVersion,
)


class CodexHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Codex CLI."""

    provider: ClassVar[Provider] = Provider.CODEX

    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {
        "codexDefaultModel": "gpt",
        "codexDefaultEffort": "medium",
        "codexDefaultPermissionMode": "read_only",
        "codexDefaultContextMax": 272_000,
    }

    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = {
        AgentSettingCategory.LIVE: [],
        AgentSettingCategory.IDLE: [
            "selected_model",
            "effort",
            "permission_mode",
            "context_max",
        ],
        AgentSettingCategory.STARTUP: [],
    }

    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = {
        "selected_model": "codexDefaultModel",
        "effort": "codexDefaultEffort",
        "permission_mode": "codexDefaultPermissionMode",
        "context_max": "codexDefaultContextMax",
    }

    # No external sync (no usage tracking, no OpenRouter pricing) for now —
    # this is intentional, the first cut keeps Codex purely declarative.
    USAGE_SYNC_INTERVAL: ClassVar[int | None] = None
    OPENROUTER_MODEL_PREFIX: ClassVar[str | None] = None

    # Single supported model. ``selected_model_value`` will return ``"gpt"``
    # for this entry (latest of its family), matching the Claude Code
    # convention of bare-alias-for-latest / versioned-alias for the rest.
    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = [
        ModelVersion(
            provider=Provider.CODEX,
            model="gpt",
            version="5.5",
            full_name="gpt-5.5",
            retirement_date=None,
            latest=True,
            provider_extra=None,
        ),
    ]
