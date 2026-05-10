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
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import orjson
from django.conf import settings

from twicc.core.enums import Provider
from twicc.pricing import FamilyPrices
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    UserMessage,
)

from .pricing import extract_model_info

# Wrapper-level types and their tool-result payload sub-types. Mirrors
# the discovery rules in ``codex.compute``; kept inline to avoid a
# cross-module dependency for these tiny lookups. Two shapes carry a
# tool_result and pair with their function_call by ``call_id``:
# - ``response_item.{function_call_output, custom_tool_call_output}`` —
#   the LLM-facing output string of a standard / custom function call.
# - ``event_msg.*`` whose sub-type is in :data:`_PERSISTED_END_EVENT_TYPES`
#   — patch_apply_end, mcp_tool_call_end, web_search_end,
#   image_generation_end. ``exec_command_end`` is intentionally absent:
#   Codex CLI no longer persists it, so we reconstruct shell transcripts
#   from the chain of function_call_output rows instead.
_TYPE_RESPONSE_ITEM = "response_item"
_TYPE_EVENT_MSG = "event_msg"
_RESPONSE_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
_PERSISTED_END_EVENT_TYPES = frozenset({
    "patch_apply_end",
    "mcp_tool_call_end",
    "web_search_end",
    "image_generation_end",
})

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

    OPENROUTER_MODEL_PREFIX: ClassVar[str | None] = "openai/"

    # Per-family default prices (USD per million tokens) — fallback when no
    # ``ModelPrice`` row matches and no other version of the same family is
    # in the DB. Restricted to the families actually observed in Codex CLI
    # JSONLs (``gpt`` / ``gpt-codex`` / ``gpt-codex-max``); other OpenAI
    # families (``gpt-mini``, ``gpt-pro``, …) get parsed correctly by
    # :meth:`extract_family_and_version` but rely entirely on the synced
    # OpenRouter rows since Codex CLI doesn't run them today. OpenRouter
    # doesn't expose a separate ``input_cache_write`` price for OpenAI
    # models, so the cache-write defaults are zero.
    DEFAULT_FAMILY_PRICES: ClassVar[dict[str, FamilyPrices]] = {
        "gpt": FamilyPrices(  # baseline gpt-5.4 pricing as of 2026-05
            input_price=Decimal("2.50"),
            output_price=Decimal("15.00"),
            cache_read_price=Decimal("0.25"),
            cache_write_5m_price=Decimal("0"),
            cache_write_1h_price=Decimal("0"),
        ),
        "gpt-codex": FamilyPrices(  # gpt-5-codex pricing
            input_price=Decimal("1.25"),
            output_price=Decimal("10.00"),
            cache_read_price=Decimal("0.125"),
            cache_write_5m_price=Decimal("0"),
            cache_write_1h_price=Decimal("0"),
        ),
        "gpt-codex-max": FamilyPrices(  # gpt-5.1-codex-max pricing
            input_price=Decimal("1.25"),
            output_price=Decimal("10.00"),
            cache_read_price=Decimal("0.125"),
            cache_write_5m_price=Decimal("0"),
            cache_write_1h_price=Decimal("0"),
        ),
    }

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

        Classifies user/assistant messages, tool_use lines
        (function_call / custom_tool_call), and pairs each tool_use with
        its result through the inherited ``ToolResultLink`` machinery.
        Persisted ``event_msg`` ends in :data:`_PERSISTED_END_EVENT_TYPES`
        (``patch_apply_end``, ``mcp_tool_call_end``, ``web_search_end``,
        ``image_generation_end``) get their own ``ToolResultLink`` row
        alongside the matching ``function_call_output``.
        ``exec_command_end`` is no longer persisted by Codex CLI, so for
        long-running shells we instead chain the parent ``exec_command``
        and every ``write_stdin`` polling output onto a single
        ``tool_use_id`` via :meth:`CodexSessionCompute.remap_tool_result_id`.
        Every other JSONL line goes to ``ItemKind.SYSTEM``. Bump this
        constant when the pipeline learns a new mapping so existing
        sessions are recomputed.
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

    def extract_family_and_version(
        self, model_id: str,
    ) -> tuple[str | None, str | None]:
        """Parse an OpenAI OpenRouter ``model_id`` into ``(family, version)``.

        Handles the layout used by every OpenAI entry on OpenRouter:
        ``openai/gpt-<version>[-<variant>...]`` where ``<version>`` is
        the dotted-numeric portion (``"5"``, ``"5.1"``, ``"5.4"``) and
        the optional variant suffix names a pricing-equivalence bucket
        folded into the family so siblings under the same variant
        share a single fallback bucket:

        - ``openai/gpt-5-codex``       → ``("gpt-codex", "5")``
        - ``openai/gpt-5.1-codex-max`` → ``("gpt-codex-max", "5.1")``
        - ``openai/gpt-5.4``           → ``("gpt", "5.4")``
        - ``openai/gpt-5.4-mini``      → ``("gpt-mini", "5.4")``
        - ``openai/gpt-5.5-pro``       → ``("gpt-pro", "5.5")``

        Returns ``(None, None)`` for ids that don't follow the
        ``gpt-<version>...`` pattern: missing ``openai/`` prefix, bare
        prefix without a body, non-numeric first segment
        (``openai/gpt-audio``, ``openai/gpt-chat-latest``,
        ``openai/gpt-oss-120b``), version mixing digits with letters
        (``openai/gpt-4o``, ``openai/gpt-4o-mini``), or anything outside
        the ``openai/gpt-`` namespace (``openai/o3-deep-research``,
        ``anthropic/claude-opus-4.5``).
        """
        prefix = "openai/"
        if not model_id.startswith(prefix):
            return None, None
        info = extract_model_info(model_id.removeprefix(prefix))
        if info is None:
            return None, None
        return info.family, info.version

    def serialize_model(self, model: str | None) -> dict | None:
        """Serialize a Codex model identifier as ``{raw, family, version}``.

        Returns ``None`` for an empty input. Defers to
        :func:`extract_model_info` so the wire shape matches the
        pricing-equivalence buckets used by
        :meth:`extract_family_and_version` (e.g. ``"gpt-5-codex"`` →
        family ``"gpt-codex"`` on both sides). Falls back to
        ``{raw, None, None}`` when the format is not recognised.
        """
        if not model:
            return None
        info = extract_model_info(model)
        if info is None:
            return {"raw": model, "family": None, "version": None}
        return {"raw": model, "family": info.family, "version": info.version}

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

    # ------------------------------------------------------------------
    # Tool results
    # ------------------------------------------------------------------

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        """Return the tool-result payloads matching ``tool_use_id``.

        Two line shapes can carry a tool_result paired by ``call_id``:
        ``response_item.{function_call_output, custom_tool_call_output}``
        (the LLM-facing string — for long-running shells, multiple of
        these chain together via :meth:`CodexSessionCompute.remap_tool_result_id`)
        and ``event_msg`` lines whose sub-type is in
        :data:`_PERSISTED_END_EVENT_TYPES` (the structured End events
        we still consume). Each is kept as its own
        :class:`ToolResultLink` row, so ``items`` typically contains
        every chunk plus the structured end event when applicable.

        Callers already filtered ``items`` to the lines linked via
        :class:`ToolResultLink` for this ``tool_use_id``; we parse each
        one and return the payload as-is. The frontend's
        ``JsonHumanView`` fallback renders it without further hints.
        """
        results: list[dict] = []
        for item in items:
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            wrapper_type = parsed.get("type")
            payload = parsed.get("payload")
            if not isinstance(payload, dict):
                continue
            if wrapper_type == _TYPE_RESPONSE_ITEM:
                if payload.get("type") not in _RESPONSE_TOOL_RESULT_PAYLOAD_TYPES:
                    continue
            elif wrapper_type == _TYPE_EVENT_MSG:
                # Only structured End events from the whitelist count —
                # mirror the compute side's :func:`_event_msg_call_id`.
                if payload.get("type") not in _PERSISTED_END_EVENT_TYPES:
                    continue
                event_call_id = payload.get("call_id")
                if not isinstance(event_call_id, str) or not event_call_id:
                    continue
            else:
                continue
            if payload.get("call_id") != tool_use_id:
                continue
            results.append(payload)
        return results
