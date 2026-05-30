"""Django-free constants for the Claude Code provider.

Holds the pure data tables (settings defaults, field-to-key mapping,
settings categories, model version registry) so they can be imported by
lightweight callers — such as ``twicc create-session`` for its ``--help``
enrichment — without triggering ``django.setup()`` via the sibling
``helpers.py``.

The :class:`ClaudeCodeHelpers` class re-exposes these as ``ClassVar`` for
backward compatibility with existing ``self.SYNCED_SETTINGS_DEFAULTS`` /
``self.AGENT_SETTINGS_FIELDS_MAPPING`` / ``self.MODEL_VERSIONS`` access
patterns.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion


class ClaudeCodeModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Claude Code."""
    supports_1m: bool
    supports_effort_xhigh: bool
    supports_effort_max: bool
    supports_fast: bool
    support_permission_auto: bool


SYNCED_SETTINGS_DEFAULTS: dict = {
    "claudeCodeDefaultPermissionMode": "default",
    "claudeCodeDefaultModel": "opus",
    "claudeCodeDefaultEffort": "medium",
    "claudeCodeDefaultThinking": True,
    "claudeCodeDefaultClaudeInChrome": True,
    "claudeCodeDefaultFastMode": False,
    "claudeCodeDefaultContextMax": 200_000,
    "claudeCodeUsageReadFileEnabled": False,
    "claudeCodeUsageReadFilePath": "",
    "claudeCodeUsageDumpFileEnabled": False,
    "claudeCodeUsageDumpFilePath": "",
}


RENAMED_SYNCED_SETTINGS_KEYS: dict[str, str] = {
    "defaultPermissionMode": "claudeCodeDefaultPermissionMode",
    "defaultModel": "claudeCodeDefaultModel",
    "defaultEffort": "claudeCodeDefaultEffort",
    "defaultThinking": "claudeCodeDefaultThinking",
    "defaultClaudeInChrome": "claudeCodeDefaultClaudeInChrome",
    "defaultContextMax": "claudeCodeDefaultContextMax",
    "usageJsonFileEnabled": "claudeCodeUsageReadFileEnabled",
    "usageJsonFilePath": "claudeCodeUsageReadFilePath",
    "usageDumpFileEnabled": "claudeCodeUsageDumpFileEnabled",
    "usageDumpFilePath": "claudeCodeUsageDumpFilePath",
}


OBSOLETE_SYNCED_SETTINGS_KEYS: tuple[str, ...] = (
    "alwaysApplyDefaultPermissionMode",
    "alwaysApplyDefaultModel",
    "alwaysApplyDefaultEffort",
    "alwaysApplyDefaultThinking",
    "alwaysApplyDefaultClaudeInChrome",
    "alwaysApplyDefaultContextMax",
)


AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: ["permission_mode"],
    AgentSettingCategory.IDLE: ["selected_model", "context_max"],
    AgentSettingCategory.STARTUP: [
        "effort",
        "thinking_enabled",
        "claude_in_chrome",
        "fast_mode",
        "question_widget",
    ],
}


AGENT_SETTINGS_FIELDS_MAPPING: dict[str, str] = {
    "permission_mode": "claudeCodeDefaultPermissionMode",
    "selected_model": "claudeCodeDefaultModel",
    "effort": "claudeCodeDefaultEffort",
    "thinking_enabled": "claudeCodeDefaultThinking",
    "claude_in_chrome": "claudeCodeDefaultClaudeInChrome",
    "fast_mode": "claudeCodeDefaultFastMode",
    "context_max": "claudeCodeDefaultContextMax",
}

# ------------------------------------------------------------------
# Model registry — supported model versions
#
# The ``selected_model`` value stored in settings and session DB
# fields uses:
# - bare alias for latest: ``"opus"``, ``"sonnet"``
# - versioned alias for non-latest: ``"opus-4.5"``, ``"sonnet-4.5"``
#
# When communicating with the SDK, latest aliases are passed as-is
# (the CLI resolves them), while versioned aliases are resolved to
# their ``full_name``.
#
# Deprecations: https://platform.claude.com/docs/en/about-claude/model-deprecations
# ------------------------------------------------------------------

MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.8", full_name="claude-opus-4-8",
        retirement_date=None,
        latest=True,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=True, support_permission_auto=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.7", full_name="claude-opus-4-7",
        retirement_date=date(2027, 4, 16),
        latest=False,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=True, support_permission_auto=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.6", full_name="claude-opus-4-6",
        retirement_date=date(2027, 2, 5),
        latest=False,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            supports_fast=True, support_permission_auto=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.5", full_name="claude-opus-4-5-20251101",
        retirement_date=date(2026, 11, 24),
        latest=False,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            supports_fast=False, support_permission_auto=False,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="sonnet", version="4.6", full_name="claude-sonnet-4-6",
        retirement_date=None,  # to set when sonnet 4.7 is released (retire 2027-02-17)
        latest=True,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            supports_fast=False, support_permission_auto=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="sonnet", version="4.5", full_name="claude-sonnet-4-5-20250929",
        retirement_date=date(2026, 9, 29),
        latest=False,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            supports_fast=False, support_permission_auto=False,
        ),
    ),
]
