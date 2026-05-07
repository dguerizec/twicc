"""
Codex implementation of :class:`BaseProviderHelpers`.

Minimal first cut: Codex is registered so it can be selected as the default
provider and have its agent settings (model, effort, permission_mode,
context_max) edited from the popover and presets dialog. There is no
agent runtime yet — the front gates ``canSendMessage`` to ``False`` for
this provider, so no Codex session can actually be created.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings

from twicc.core.enums import Provider
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    UserMessage,
)

if TYPE_CHECKING:
    from twicc.core.models import SessionItem


class CodexHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Codex CLI."""

    provider: ClassVar[Provider] = Provider.CODEX

    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {
        "codexDefaultModel": "gpt",
        "codexDefaultEffort": "medium",
        "codexDefaultPermissionMode": "read_only",
        "codexDefaultContextMax": 272_000,
        "codexUsageReadFileEnabled": False,
        "codexUsageReadFilePath": "",
        "codexUsageDumpFileEnabled": False,
        "codexUsageDumpFilePath": "",
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

    # Polled every 5 minutes by ``codex.usage_task`` against ChatGPT's
    # ``/backend-api/wham/usage`` endpoint (the same one the Codex CLI's
    # /status command hits) to refresh the 5-hour and weekly quotas.
    USAGE_SYNC_INTERVAL: ClassVar[int | None] = 5 * 60

    # Filesystem source for Codex session JSONL files. The Codex CLI
    # writes one folder per ``YYYY/MM/DD`` (not per-project, unlike
    # Claude Code), with one ``rollout-*.jsonl`` per session. Read by
    # the initial sync; not exposed through the registry because it has
    # no cross-provider meaning.
    SESSIONS_DIR: ClassVar[Path] = Path.home() / ".codex" / "sessions"

    # OpenRouter pricing isn't wired yet for Codex — there's no agent
    # runtime so no per-line cost computation either.
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

    @property
    def current_compute_version(self) -> int | None:
        """Return :data:`settings.CODEX_COMPUTE_VERSION`.

        Currently ``None`` — Codex has no compute pipeline yet, so every
        Codex session is reported up-to-date with its default
        ``compute_version=NULL``. The day the pipeline lands, this
        constant moves to ``1`` (or higher) and existing sessions become
        outdated until the new compute task processes them.
        """
        return settings.CODEX_COMPUTE_VERSION

    def validate_usage_file_payload(self, payload: dict) -> tuple[bool, str]:
        """Accept a payload that has the shape of a Codex ``wham/usage`` response.

        The cross-provider envelope (``twicc.usage.validate_usage_file``)
        already asserts existence + JSON object; here we only check
        for the Codex-specific top-level ``rate_limit`` block, which is
        what :func:`twicc.providers.codex.usage.save_usage_snapshot`
        reads from. We don't drill into ``primary_window`` /
        ``secondary_window`` because both can legitimately be missing
        when the user has never consumed any quota in the matching
        window.
        """
        from .usage import USAGE_REQUIRED_KEYS

        missing = USAGE_REQUIRED_KEYS - payload.keys()
        if missing:
            return False, f"Missing required keys: {', '.join(sorted(missing))}"
        return True, "Valid Codex usage file"

    # ------------------------------------------------------------------
    # Full-text search indexing — stub (no compute / no search yet)
    # ------------------------------------------------------------------
    #
    # Codex has no compute pipeline and no parsed content shape yet, so
    # the search index has nothing to index from a Codex session. These
    # stubs return empty results to keep the cross-provider search
    # indexing task (``twicc.search_indexing_task``) from raising
    # NotImplementedError on every Codex session at startup. When the
    # Codex compute lands, real implementations replace these.

    def get_user_messages(
        self,
        items: Iterable[SessionItem],
        limit: int | None = None,
    ) -> list[UserMessage]:
        return []

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        return []

    def extract_indexable_text(self, item: SessionItem) -> str:
        return ""
