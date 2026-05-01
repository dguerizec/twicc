"""
Claude Code implementation of :class:`BaseProviderHelpers`.

Centralizes the per-provider surface that the core consumes through the
provider helpers registry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar, NamedTuple

import orjson

from twicc.core.enums import ItemKind, Provider
from twicc.providers.helpers import (
    AgentSettingCategory,
    AgentSettings,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    TitleValidationResult,
    UserMessage,
)

from .compute import get_message_content, get_message_content_list
from .pricing import extract_model_info
from .titles import rename_session_in_jsonl, validate_title

if TYPE_CHECKING:
    from twicc.core.models import SessionItem

logger = logging.getLogger(__name__)


class ClaudeCodeModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Claude Code."""
    supports_1m: bool
    supports_effort_xhigh: bool
    supports_effort_max: bool


@lru_cache(maxsize=32)
def serialize_model(model: str | None) -> dict | None:
    """Serialize a Claude model identifier as ``{raw, family, version}``.

    Returns ``None`` for an empty input. Falls back to ``{raw, None, None}``
    when the format is not recognized.
    """
    if not model:
        return None

    info = extract_model_info(model)
    if not info:
        return {"raw": model, "family": None, "version": None}

    return {
        "raw": model,
        "family": info.family,
        "version": info.version,
    }


class ClaudeCodeHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Claude Code CLI / SDK."""

    provider: ClassVar[Provider] = Provider.CLAUDE_CODE

    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {
        "claudeCodeDefaultPermissionMode": "default",
        "claudeCodeDefaultModel": "opus",
        "claudeCodeDefaultEffort": "medium",
        "claudeCodeDefaultThinking": True,
        "claudeCodeDefaultClaudeInChrome": True,
        "claudeCodeDefaultContextMax": 200_000,
        "claudeCodeUsageReadFileEnabled": False,
        "claudeCodeUsageReadFilePath": "",
        "claudeCodeUsageDumpFileEnabled": False,
        "claudeCodeUsageDumpFilePath": "",
    }

    RENAMED_SYNCED_SETTINGS_KEYS: ClassVar[dict[str, str]] = {
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

    OBSOLETE_SYNCED_SETTINGS_KEYS: ClassVar[tuple[str, ...]] = (
        "alwaysApplyDefaultPermissionMode",
        "alwaysApplyDefaultModel",
        "alwaysApplyDefaultEffort",
        "alwaysApplyDefaultThinking",
        "alwaysApplyDefaultClaudeInChrome",
        "alwaysApplyDefaultContextMax",
    )

    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = {
        AgentSettingCategory.LIVE: ["permission_mode"],
        AgentSettingCategory.IDLE: ["selected_model", "context_max"],
        AgentSettingCategory.STARTUP: ["effort", "thinking_enabled", "claude_in_chrome"],
    }

    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = {
        "permission_mode": "claudeCodeDefaultPermissionMode",
        "selected_model": "claudeCodeDefaultModel",
        "effort": "claudeCodeDefaultEffort",
        "thinking_enabled": "claudeCodeDefaultThinking",
        "claude_in_chrome": "claudeCodeDefaultClaudeInChrome",
        "context_max": "claudeCodeDefaultContextMax",
    }

    USAGE_SYNC_INTERVAL: ClassVar[int | None] = 5 * 60

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

    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = [
        ModelVersion(
            provider=Provider.CLAUDE_CODE,
            model="opus", version="4.7", full_name="claude-opus-4-7",
            retirement_date=None,  # to set when sonnet 4.8 is released (retire 2027-04-16)
            latest=True,
            provider_extra=ClaudeCodeModelExtra(
                supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            ),
        ),
        ModelVersion(
            provider=Provider.CLAUDE_CODE,
            model="opus", version="4.6", full_name="claude-opus-4-6",
            retirement_date=date(2027, 2, 5),
            latest=False,
            provider_extra=ClaudeCodeModelExtra(
                supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            ),
        ),
        ModelVersion(
            provider=Provider.CLAUDE_CODE,
            model="opus", version="4.5", full_name="claude-opus-4-5-20251101",
            retirement_date=date(2026, 11, 24),
            latest=False,
            provider_extra=ClaudeCodeModelExtra(
                supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            ),
        ),
        ModelVersion(
            provider=Provider.CLAUDE_CODE,
            model="sonnet", version="4.6", full_name="claude-sonnet-4-6",
            retirement_date=None,  # to set when sonnet 4.7 is released (retire 2027-02-17)
            latest=True,
            provider_extra=ClaudeCodeModelExtra(
                supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            ),
        ),
        ModelVersion(
            provider=Provider.CLAUDE_CODE,
            model="sonnet", version="4.5", full_name="claude-sonnet-4-5-20250929",
            retirement_date=date(2026, 9, 29),
            latest=False,
            provider_extra=ClaudeCodeModelExtra(
                supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            ),
        ),
    ]

    def find_model(self, identifier: str) -> ModelVersion | None:
        """Look up a Claude Code model by its ``selected_model`` alias.

        Accepts both bare aliases (``"opus"``) and versioned aliases
        (``"opus-4.5"``). For bare aliases, returns the latest version
        of that family; for versioned aliases, the entry whose
        ``model`` and ``version`` match.
        """
        if "-" in identifier:
            model, version = identifier.split("-", 1)
            for mv in self.MODEL_VERSIONS:
                if mv.model == model and mv.version == version:
                    return mv
            return None
        for mv in self.MODEL_VERSIONS:
            if mv.model == identifier and mv.latest:
                return mv
        return None

    def _resolve_to_default_model_version(self) -> ModelVersion | None:
        """Return the :class:`ModelVersion` for the synced default model.

        Used by capability checks as a defensive fallback when the
        caller passes ``None`` or an unknown model. Returns ``None``
        if the synced default itself is missing or unknown.
        """
        from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS, read_synced_settings
        default_model = (
            read_synced_settings().get("claudeCodeDefaultModel")
            or SYNCED_SETTINGS_DEFAULTS.get("claudeCodeDefaultModel")
        )
        if not default_model:
            return None
        return self.find_model(default_model)

    def resolve_sdk_model(self, selected_model: str | None, context_max: int) -> str | None:
        """Resolve a ``selected_model`` + ``context_max`` to the SDK model string.

        - Latest aliases (``"opus"``, ``"sonnet"``) are passed through (the CLI resolves them).
        - Versioned aliases (``"opus-4.5"``) are resolved to their ``full_name``.
        - Appends ``"[1m]"`` when ``context_max`` is 1M and the model supports it.

        Returns ``None`` for an empty input.
        """
        if not selected_model:
            return None
        mv = self.find_model(selected_model)
        if mv is None:
            logger.warning("Unknown model '%s', passing through to SDK", selected_model)
            base = selected_model
            supports_1m = True
        elif mv.latest:
            base = mv.model
            supports_1m = mv.provider_extra.supports_1m
        else:
            base = mv.full_name
            supports_1m = mv.provider_extra.supports_1m
        if context_max == 1_000_000 and supports_1m:
            return f"{base}[1m]"
        return base

    def selected_model_supports_1m(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or the synced default fallback) supports 1M context."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_1m)

    def selected_model_supports_effort_xhigh(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports the ``"xhigh"`` effort level."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_effort_xhigh)

    def selected_model_supports_effort_max(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports the ``"max"`` effort level."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_effort_max)

    def enforce_synced_settings_consistency(self, synced: dict, changes: dict) -> None:
        """Normalise ``claudeCodeDefault*`` keys when the default model changed.

        ``claudeCodeDefaultModel`` is the pivot: when the client picks
        a new default model in the UI, the front sends the related
        keys (``ContextMax``, ``Effort``) in the same update, so we
        only need to fire the rule then. Builds a transient
        :class:`AgentSettings` from the merged ``synced`` dict, runs
        it through :meth:`enforce_agent_settings_consistency`
        (auto-upgrade retired model + capability rules), and writes
        back only fields that were included in ``changes`` — never
        mutating a key the client didn't ask to touch.
        """
        if "claudeCodeDefaultModel" not in changes:
            return
        candidate = AgentSettings(
            selected_model=synced.get(
                "claudeCodeDefaultModel", self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultModel"],
            ),
            context_max=synced.get(
                "claudeCodeDefaultContextMax", self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultContextMax"],
            ),
            effort=synced.get("claudeCodeDefaultEffort"),
        )
        adjusted = self.enforce_agent_settings_consistency(candidate)
        if (
            "claudeCodeDefaultModel" in changes
            and adjusted.selected_model != candidate.selected_model
        ):
            synced["claudeCodeDefaultModel"] = adjusted.selected_model
        if (
            "claudeCodeDefaultContextMax" in changes
            and adjusted.context_max != candidate.context_max
        ):
            synced["claudeCodeDefaultContextMax"] = adjusted.context_max
        if (
            "claudeCodeDefaultEffort" in changes
            and adjusted.effort != candidate.effort
        ):
            synced["claudeCodeDefaultEffort"] = adjusted.effort

    def enforce_agent_settings_consistency(self, settings: AgentSettings) -> AgentSettings:
        """Auto-upgrade retired model, then normalise capability rules.

        Pipeline:
        1. Delegates to :meth:`BaseProviderHelpers.enforce_agent_settings_consistency`
           to substitute a retired ``selected_model`` with its successor.
        2. Caps ``context_max`` to 200K when the (post-upgrade) model
           doesn't support 1M context.
        3. Demotes ``effort == "max"`` to ``"xhigh"`` (or ``"high"`` if
           xhigh is also unsupported), then ``effort == "xhigh"`` to
           ``"high"`` when unsupported.
        """
        settings = super().enforce_agent_settings_consistency(settings)

        model = settings.selected_model
        context_max = settings.context_max
        effort = settings.effort

        if context_max == 1_000_000 and not self.selected_model_supports_1m(model):
            context_max = 200_000

        if effort == "max" and not self.selected_model_supports_effort_max(model):
            effort = "xhigh" if self.selected_model_supports_effort_xhigh(model) else "high"
        if effort == "xhigh" and not self.selected_model_supports_effort_xhigh(model):
            effort = "high"

        if context_max == settings.context_max and effort == settings.effort:
            return settings
        return settings._replace(context_max=context_max, effort=effort)

    def serialize_model_extra(self, mv: ModelVersion) -> dict:
        """Expose Claude Code's :class:`ClaudeCodeModelExtra` flags on the wire."""
        return mv.provider_extra._asdict()

    def get_user_messages(
        self,
        items: Iterable[SessionItem],
        limit: int | None = None,
    ) -> list[UserMessage]:
        from twicc.search import extract_indexable_text

        out: list[UserMessage] = []
        for item in items:
            if limit is not None and len(out) >= limit:
                break
            try:
                parsed = orjson.loads(item.content)
            except (orjson.JSONDecodeError, TypeError):
                continue
            text = extract_indexable_text(get_message_content(parsed))
            if text:
                out.append(UserMessage(
                    line_num=item.line_num,
                    timestamp=item.timestamp,
                    text=text,
                ))
        return out

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        from twicc.search import extract_indexable_text

        out: list[IndexableMessage] = []
        for item in items:
            try:
                parsed = orjson.loads(item.content)
            except (orjson.JSONDecodeError, TypeError):
                continue
            text = extract_indexable_text(get_message_content(parsed))
            if text:
                from_role = "user" if item.kind == ItemKind.USER_MESSAGE else "assistant"
                out.append(IndexableMessage(
                    line_num=item.line_num,
                    text=text,
                    from_role=from_role,
                    timestamp=item.timestamp,
                ))
        return out

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        results: list[dict] = []
        for item in items:
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            content_list = get_message_content_list(parsed, "user")
            if not content_list:
                continue
            for entry in content_list:
                if entry.get("type") == "tool_result" and entry.get("tool_use_id") == tool_use_id:
                    results.append(entry)
        return results

    def serialize_model(self, model: str | None) -> dict | None:
        return serialize_model(model)

    async def generate_title(self, prompt: str, system_prompt: str) -> str | None:
        """Run a short Haiku SDK query to suggest a title for ``prompt``."""
        from .title_suggest import generate_title as _generate

        return await _generate(prompt, system_prompt)

    def validate_title(self, title: str | None) -> TitleValidationResult:
        normalized, error = validate_title(title)
        return TitleValidationResult(title=normalized, error=error)

    def rename_session(self, session_id: str, title: str) -> None:
        rename_session_in_jsonl(session_id, title)

    def get_bootstrap_data(self) -> dict:
        from .claude_settings_presets import read_claude_settings_presets

        return {
            "agent_settings_presets": read_claude_settings_presets(),
            "agent_settings_categories": {
                category.value: keys
                for category, keys in self.AGENT_SETTINGS_CATEGORIES.items()
            },
            "model_registry": self.serialize_model_registry(),
        }
