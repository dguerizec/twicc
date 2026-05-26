"""
Codex implementation of :class:`BaseProviderHelpers`.

Centralizes the per-provider surface that the core consumes through the
provider helpers registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import orjson
from django.conf import settings

from twicc.core.enums import ItemKind, Provider
from twicc.pricing import FamilyPrices
from twicc.providers.helpers import (
    AgentSettingCategory,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    UserMessage,
)

from .constants import (
    AGENT_SETTINGS_CATEGORIES as _AGENT_SETTINGS_CATEGORIES,
    AGENT_SETTINGS_FIELDS_MAPPING as _AGENT_SETTINGS_FIELDS_MAPPING,
    MODEL_VERSIONS as _MODEL_VERSIONS,
    SYNCED_SETTINGS_DEFAULTS as _SYNCED_SETTINGS_DEFAULTS,
)
from .pricing import extract_model_info
from .streaming_registry import get_streamed_item_registry

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
# event_msg sub-types that carry an indexable chat message body. Mirrors
# the matching constants in ``codex.compute``; kept inline for the same
# reason as the tool-result lookups above.
_PAYLOAD_USER_MESSAGE = "user_message"
_PAYLOAD_AGENT_MESSAGE = "agent_message"
_INDEXABLE_PAYLOAD_TYPES = frozenset({_PAYLOAD_USER_MESSAGE, _PAYLOAD_AGENT_MESSAGE})

if TYPE_CHECKING:
    from twicc.core.models import SessionItem


AGENT_SETTINGS_CHOICES: dict[str, list] = {
    "effort": ["low", "medium", "high", "xhigh"],
    "permission_mode": ["read_only", "strict", "auto", "autonomous", "yolo"],
    "context_max": [272_000],
}


ATTACHMENT_SUPPORT: dict = {
    "images": True,
    "documents": False,
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}


class CodexHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Codex CLI."""

    provider: ClassVar[Provider] = Provider.CODEX

    # Constants are defined in ``.constants`` (Django-free module) so the
    # CLI's ``--help`` enrichment can read them without ``django.setup()``.
    # Re-exposed as ClassVars to preserve the existing ``self.XXX`` access
    # patterns used throughout the codebase.
    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = _SYNCED_SETTINGS_DEFAULTS
    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = _AGENT_SETTINGS_CATEGORIES
    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = _AGENT_SETTINGS_FIELDS_MAPPING

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

    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = _MODEL_VERSIONS

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

    def get_agent_settings_choices(self) -> dict[str, list]:
        return AGENT_SETTINGS_CHOICES

    def get_attachment_support(self) -> dict:
        return ATTACHMENT_SUPPORT

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
    # Model registry — alias resolution
    # ------------------------------------------------------------------

    def find_model(self, identifier: str) -> ModelVersion | None:
        """Look up a Codex model by its ``selected_model`` alias.

        ``Session.selected_model`` (and the WS / preset wire) stores the
        compact alias produced by :meth:`selected_model_value`, not the
        SDK ``full_name``:

        - ``"gpt"``       → latest of family ``gpt`` (gpt-5.5 today)
        - ``"gpt-5.4"``   → versioned alias (family=gpt, version=5.4)
        - ``"gpt-mini"``  → latest of family ``gpt-mini`` (gpt-5.4-mini today)

        We check the bare alias first to disambiguate family names that
        themselves contain a dash (``"gpt-mini"``), then fall back to the
        versioned form. The base implementation matches on ``full_name``
        which would never hit our alias-shaped identifiers.
        """
        for mv in self.MODEL_VERSIONS:
            if mv.latest and mv.model == identifier:
                return mv
        for mv in self.MODEL_VERSIONS:
            if identifier == f"{mv.model}-{mv.version}":
                return mv
        return None

    def resolve_sdk_model(self, selected_model: str | None) -> str | None:
        """Resolve a ``selected_model`` alias to the Codex SDK model name.

        The SDK's ``thread_start(model=...)`` expects an exact id such as
        ``"gpt-5.5"`` or ``"gpt-5.4-mini"``, but our wire-side identifiers
        are aliases (see :meth:`find_model`). When the alias matches a
        known entry, return its ``full_name``; otherwise pass the input
        through so a hand-typed value still reaches the CLI (which will
        reject it server-side with a clear error if unknown).
        """
        if not selected_model:
            return None
        mv = self.find_model(selected_model)
        return mv.full_name if mv else selected_model

    async def generate_title(self, prompt: str, system_prompt: str) -> str | None:
        """Run a short gpt-5.4-mini SDK query to suggest a title for ``prompt``."""
        from .title_suggest import generate_title as _generate

        return await _generate(prompt, system_prompt)

    def rename_session(self, session_id: str, title: str) -> None:
        """Persist the new title in Codex's state DB via ``thread/name/set``.

        The Codex SDK is async-first; we hop into an event loop with
        ``async_to_sync`` so the sync REST handler in ``views.py``
        keeps its current signature. No JSONL append and no
        ``protect_title`` machinery: the Codex thread name lives in
        its own state DB, the rollout JSONL never carries it, and no
        side actor can re-append a stale value.
        """
        from asgiref.sync import async_to_sync

        from .titles import rename_thread_via_sdk

        async_to_sync(rename_thread_via_sdk)(session_id, title)

    # ------------------------------------------------------------------
    # Full-text search indexing
    # ------------------------------------------------------------------
    #
    # Codex stores chat message bodies as a flat ``payload.message``
    # string on ``event_msg.user_message`` / ``event_msg.agent_message``
    # lines (no content array, no nested blocks — see
    # :func:`codex.compute._event_msg_text`). We use that single string
    # both as the searchable text and as the title-suggest input.

    def extract_indexable_text(self, item: SessionItem) -> str:
        try:
            parsed = orjson.loads(item.content)
        except (orjson.JSONDecodeError, TypeError):
            return ""
        if not isinstance(parsed, dict) or parsed.get("type") != _TYPE_EVENT_MSG:
            return ""
        payload = parsed.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in _INDEXABLE_PAYLOAD_TYPES:
            return ""
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message
        return ""

    def get_user_messages(
        self,
        items: Iterable[SessionItem],
        limit: int | None = None,
    ) -> list[UserMessage]:
        out: list[UserMessage] = []
        for item in items:
            if limit is not None and len(out) >= limit:
                break
            text = self.extract_indexable_text(item)
            if text:
                out.append(UserMessage(
                    line_num=item.line_num,
                    timestamp=item.timestamp,
                    text=text,
                ))
        return out

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        out: list[IndexableMessage] = []
        for item in items:
            text = self.extract_indexable_text(item)
            if not text:
                continue
            from_role = "user" if item.kind == ItemKind.USER_MESSAGE else "assistant"
            out.append(IndexableMessage(
                line_num=item.line_num,
                text=text,
                from_role=from_role,
                timestamp=item.timestamp,
            ))
        return out

    # ------------------------------------------------------------------
    # Tool results
    # ------------------------------------------------------------------

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        """Return the tool-result payloads matching ``tool_use_id``.

        Three line shapes can carry a tool_result paired by ``call_id``
        (or rebound to one):

        - ``response_item.{function_call_output, custom_tool_call_output}``
          (the LLM-facing string — for long-running shells, every
          ``write_stdin`` polling output is rebound to the parent
          ``exec_command``'s ``call_id`` via
          :meth:`CodexSessionCompute.remap_tool_result_id`, so the
          payload's own ``call_id`` legitimately diverges from
          ``tool_use_id`` on every chunk past the first).
        - ``event_msg`` lines whose sub-type is in
          :data:`_PERSISTED_END_EVENT_TYPES` (the structured End events
          we still consume). These are never rebound, so the payload's
          ``call_id`` always equals ``tool_use_id``.
        - ``response_item.message role=user`` whose text body opens with
          ``<subagent_notification>``: synthetic tool_result for the
          originating ``spawn_agent``, rebound by
          :meth:`CodexSessionCompute.remap_tool_result_id` /
          :meth:`remap_tool_result_id_live`. Its payload doesn't carry
          a ``call_id`` at all (the rebind is purely DB-side).

        Callers already filtered ``items`` to the lines linked via
        :class:`ToolResultLink` for this ``tool_use_id``, which is the
        authoritative mapping once any DB-side rebind kicks in. We
        therefore trust the link table and skip a payload-level
        ``call_id`` re-check on the rebound shapes; we keep it only on
        the non-rebound ``event_msg`` shape as a defensive mirror of
        :func:`_event_msg_call_id`. Each surviving payload is returned
        as-is so the frontend can dispatch on the payload's own
        ``type`` (the wrapper ``type`` is dropped).
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
                payload_type = payload.get("type")
                if payload_type in _RESPONSE_TOOL_RESULT_PAYLOAD_TYPES:
                    # No payload-level call_id check: write_stdin polling
                    # outputs land here with their own call_id but
                    # ToolResultLink already rebound them to the parent
                    # exec_command's tool_use_id.
                    pass
                elif payload_type == "message" and payload.get("role") == "user":
                    # Subagent notification — same: rebound DB-side and
                    # no call_id on this shape anyway.
                    pass
                else:
                    continue
            elif wrapper_type == _TYPE_EVENT_MSG:
                # Only structured End events from the whitelist count —
                # mirror the compute side's :func:`_event_msg_call_id`.
                if payload.get("type") not in _PERSISTED_END_EVENT_TYPES:
                    continue
                event_call_id = payload.get("call_id")
                if not isinstance(event_call_id, str) or not event_call_id:
                    continue
                if event_call_id != tool_use_id:
                    continue
            else:
                continue
            results.append(payload)
        return results

    def enrich_live_items_payload(self, session_id: str, items: list[dict]) -> None:
        """Attach ``stream_uuid`` to each newly broadcast streaming-eligible item.

        The Codex agent pushes one SDK ``item_id`` per completed
        ``agentMessage`` *and* per completed ``reasoning`` with non-empty
        summary onto the :class:`StreamedItemRegistry`. Here we pop one
        entry per matching payload in the order the watcher serialized
        them — the FIFO invariant (single Codex CLI process emits SDK
        events and JSONL lines in lockstep) makes that pairing safe
        without comparing text or timestamps.

        The two streaming-eligible kinds are
        :class:`ItemKind.ASSISTANT_MESSAGE` (``event_msg.agent_message``)
        and :class:`ItemKind.REASONING` (``response_item.reasoning`` with
        a non-empty ``summary``). Both share a single FIFO queue: the
        agent pushes them in the same order the watcher will see the
        JSONL lines, so a single ``pop_next`` per matching item keeps
        the pairing aligned across kinds.

        ``pop_next`` returning ``None`` is the expected path on sessions
        synced outside a live agent run (initial sync, recompute, agent
        already dead): we simply don't attach ``stream_uuid`` and the
        frontend has no placeholder to retire anyway. The sibling
        ``response_item.message`` line for an exchange is bucketed as
        ``SYSTEM``/``DEBUG_ONLY`` by :meth:`compute_item_kind` and a
        reasoning line whose summary is empty is bucketed the same way,
        so neither consumes the queue.
        """
        if not items:
            return
        registry = get_streamed_item_registry()
        streaming_kinds = {ItemKind.ASSISTANT_MESSAGE, ItemKind.REASONING}
        for item in items:
            if item.get("kind") not in streaming_kinds:
                continue
            stream_uuid = registry.pop_next(session_id)
            if stream_uuid is not None:
                item["stream_uuid"] = stream_uuid

    async def try_handle_async_job(self, job, settle_async_job) -> bool:
        """Route Codex-specific async-queue jobs to their handler.

        Currently :class:`SyncSessionTitlesJob` only (boot-time bulk title
        import from ``thread_list``). Defined alongside the type, the sync
        apply, and the post-apply broadcast in :mod:`.titles`.

        Pattern: delegate the sync apply to ``settle_async_job`` (which
        runs it in ``transaction.atomic`` on a worker thread and resolves
        ``job.future`` with the result or exception), then read the
        result off ``job.future`` and run the async broadcast as a
        post-apply side effect. Skip the broadcast if the future carries
        an exception — it will propagate to the producer through
        ``submit_async_job``'s ``await asyncio.shield(job.future)``.
        """
        from .titles import (
            SyncSessionTitlesJob,
            _apply_sync_session_titles_job,
            _broadcast_changed_titles,
        )

        if isinstance(job, SyncSessionTitlesJob):
            await settle_async_job(
                job, _apply_sync_session_titles_job, "title sync",
            )
            if not job.future.cancelled() and job.future.exception() is None:
                changed = job.future.result()
                if changed:
                    try:
                        await _broadcast_changed_titles(changed)
                    except Exception as e:
                        logger.error(
                            "Title sync broadcast failed: %s", e, exc_info=True,
                        )
                    logger.info("DB writer: %d title(s) applied", len(changed))
            return True
        return False
