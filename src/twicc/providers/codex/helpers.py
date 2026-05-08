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

import orjson
from django.conf import settings

from twicc.core.enums import Provider
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    UserMessage,
)

# Wrapper-level types and their tool-result payload sub-types. Mirrors
# the discovery rules in ``codex.compute``; kept inline to avoid a
# cross-module dependency for these tiny lookups. Two shapes carry a
# tool_result and pair with their function_call by ``call_id``:
# - ``response_item.{function_call_output, custom_tool_call_output}`` —
#   the LLM-facing output string of a standard / custom function call.
# - ``event_msg.*`` with a non-empty ``payload.call_id`` — any persisted
#   ``*End`` / ``*Response`` event (exec_command_end, patch_apply_end,
#   mcp_tool_call_end, web_search_end, image_generation_end,
#   collab_*_end, dynamic_tool_call_response, …) carrying the structured
#   outcome. ``rollout/src/policy.rs`` only persists the End shape, so
#   the ``call_id`` presence on a persisted event_msg is sufficient.
_TYPE_RESPONSE_ITEM = "response_item"
_TYPE_EVENT_MSG = "event_msg"
_RESPONSE_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})

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

        Classifies user/assistant messages, tool_use lines (function_call
        / custom_tool_call, except ``write_stdin``), and pairs each
        tool_use with its result through the inherited ``ToolResultLink``
        machinery. Any persisted ``event_msg.*`` carrying a ``call_id``
        (the End / Response shape: ``exec_command_end``,
        ``patch_apply_end``, ``mcp_tool_call_end``, …) gets its own
        ``ToolResultLink`` row alongside the matching
        ``function_call_output`` — both coexist for the same call_id.
        The front decides whether to keep the running spinner via
        ``getExpectedResultCount`` (a tool with a ``*_end`` event waits
        for both rows). Every other JSONL line goes to
        ``ItemKind.SYSTEM``. Costs, runtime environment fields, and
        reasoning items are not yet processed. Bump this constant when
        the pipeline learns a new mapping so existing sessions are
        recomputed.
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

    def serialize_model(self, model: str | None) -> dict | None:
        """Serialize a Codex model identifier as ``{raw, family, version}``.

        Returns ``None`` for an empty input. The compute pipeline does
        not extract Codex models in V1, so ``session.model`` is always
        ``None`` for now — but ``serialize_session`` still calls this
        on every broadcast, so we need a real (no-op) implementation.

        For non-empty strings, falls back to a generic split on the
        first ``-`` (e.g. ``gpt-5-codex`` → family ``gpt``, version
        ``5-codex``). Replace this with a proper Codex model parser
        when the compute pipeline learns to extract the active model.
        """
        if not model:
            return None
        family, sep, version = model.partition("-")
        return {
            "raw": model,
            "family": family or None,
            "version": version if sep else None,
        }

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
        (the LLM-facing string) and ``event_msg.*`` with a non-empty
        ``call_id`` (any persisted End / Response event with the
        structured outcome). Both are kept as separate
        :class:`ToolResultLink` rows for the same call_id, so ``items``
        typically contains both when both shapes arrived.

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
                # Persisted event_msg with a call_id is an End / Response
                # event; mirror the discovery rule used on the compute side.
                event_call_id = payload.get("call_id")
                if not isinstance(event_call_id, str) or not event_call_id:
                    continue
            else:
                continue
            if payload.get("call_id") != tool_use_id:
                continue
            results.append(payload)
        return results
