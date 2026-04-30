"""
Claude Code implementation of :class:`BaseProviderHelpers`.

Centralizes the per-provider surface that the core consumes through the
provider helpers registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar

import orjson

from twicc.core.enums import ItemKind
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    IndexableMessage,
    TitleValidationResult,
    UserMessage,
)

from .compute import get_message_content, get_message_content_list
from .pricing import extract_model_info
from .titles import rename_session_in_jsonl, validate_title

if TYPE_CHECKING:
    from twicc.core.models import SessionItem


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

    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {
        "claudeCodeDefaultPermissionMode": "default",
        "claudeCodeDefaultModel": "opus",
        "claudeCodeDefaultEffort": "medium",
        "claudeCodeDefaultThinking": True,
        "claudeCodeDefaultClaudeInChrome": True,
        "claudeCodeDefaultContextMax": 200_000,
    }

    RENAMED_SYNCED_SETTINGS_KEYS: ClassVar[dict[str, str]] = {
        "defaultPermissionMode": "claudeCodeDefaultPermissionMode",
        "defaultModel": "claudeCodeDefaultModel",
        "defaultEffort": "claudeCodeDefaultEffort",
        "defaultThinking": "claudeCodeDefaultThinking",
        "defaultClaudeInChrome": "claudeCodeDefaultClaudeInChrome",
        "defaultContextMax": "claudeCodeDefaultContextMax",
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

    def get_user_messages(self, items: Iterable[SessionItem]) -> list[UserMessage]:
        from twicc.search import extract_indexable_text

        out: list[UserMessage] = []
        for item in items:
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

    def validate_title(self, title: str | None) -> TitleValidationResult:
        normalized, error = validate_title(title)
        return TitleValidationResult(title=normalized, error=error)

    def rename_session(self, session_id: str, title: str) -> None:
        rename_session_in_jsonl(session_id, title)

    def get_bootstrap_data(self) -> dict:
        from .claude_settings_presets import read_claude_settings_presets
        from .model_registry import serialize_model_registry

        return {
            "agent_settings_presets": read_claude_settings_presets(),
            "agent_settings_categories": {
                category.value: keys
                for category, keys in self.AGENT_SETTINGS_CATEGORIES.items()
            },
            "model_registry": serialize_model_registry(),
        }
