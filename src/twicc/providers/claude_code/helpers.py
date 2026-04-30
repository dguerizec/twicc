"""
Claude Code implementation of :class:`BaseProviderHelpers`.

Centralizes the per-provider surface that the core consumes through the
provider helpers registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING

import orjson

from twicc.core.enums import ItemKind
from twicc.providers.helpers import (
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
        from twicc.synced_settings import CLAUDE_SETTINGS_CATEGORIES

        from .claude_settings_presets import read_claude_settings_presets
        from .model_registry import serialize_model_registry

        return {
            "claude_settings_presets": read_claude_settings_presets(),
            "claude_settings_categories": CLAUDE_SETTINGS_CATEGORIES,
            "model_registry": serialize_model_registry(),
        }
