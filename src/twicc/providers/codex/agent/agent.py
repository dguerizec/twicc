"""
Codex agent: wraps a single AsyncCodex thread for one TwiCC session.

Minimal v1 implementation. Streaming partial output to the frontend is left
out: the watcher picks up the JSONL file the Codex CLI writes and pushes it
through the regular session_item path, so the UI catches up at end-of-turn
granularity. Approvals: the agent installs a sync ↔ async bridge on the SDK's private
``_client._sync._approval_handler`` slot and routes the 3 Codex approval
methods (commandExecution, fileChange, permissions) through the shared
``BaseAgent._await_pending_request`` plumbing. Whether approvals actually
fire depends on the resolved ``permission_mode`` for the session — the
default (``auto`` = ``workspace-write`` + ``on-request``) does emit them;
the ``yolo`` opt-out (``danger_full_access`` + ``never``) keeps the bridge
dormant.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from typing import Any, ClassVar

from channels.layers import get_channel_layer

from codex_app_server import (
    AsyncCodex,
    AsyncThread,
    AsyncTurnHandle,
    ImageInput,
    InputItem,
    ReasoningEffort,
    TextInput,
    TransportClosedError,
)

from twicc.agent import AgentState, BaseAgent, StateChangeCallback
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

from ..permission_modes import resolve_codex_turn_overrides
from ..streaming_registry import get_streamed_item_registry
from .approvals import (
    default_response_for,
    is_approval_method,
    make_pending_request,
)

logger = logging.getLogger(__name__)


def _agent_message_item(payload: Any) -> Any | None:
    """Unwrap a ``ThreadItem`` payload to its ``AgentMessageThreadItem`` inner.

    ``ItemStarted`` / ``ItemCompleted`` notifications carry the freshly
    minted (or finalized) ``ThreadItem`` under ``payload.item``. That
    ``ThreadItem`` is a Pydantic ``RootModel`` whose actual variant lives
    on ``.root`` — ``item.type`` is *not* a passthrough, it returns
    ``None``. We need the real inner instance to read ``type``/``id``.

    Returns the unwrapped instance only when it's an ``agentMessage``;
    any other type (reasoning, command_execution, plan, …) flows through
    the JSONL → watcher path and isn't streamed live in this iteration.
    """
    item = getattr(payload, "item", None)
    if item is None:
        return None
    inner = getattr(item, "root", item)
    if getattr(inner, "type", None) != "agentMessage":
        return None
    return inner


class CodexAgent(BaseAgent):
    """Codex SDK agent wrapping one ``AsyncCodex`` / ``AsyncThread`` pair.

    State machine:

    - ``STARTING`` → ``ASSISTANT_TURN``: ``start(text)`` flips the state and
      schedules a background task that runs the first turn. We don't await
      the turn inside ``start`` because ``_register_and_start`` calls it
      under the manager's lock, and a turn can run for minutes.
    - ``ASSISTANT_TURN`` → ``USER_TURN``: the turn task finishes its run.
    - ``USER_TURN`` → ``ASSISTANT_TURN``: ``send(text)`` schedules a new turn.
    - any → ``DEAD``: ``interrupt_or_kill`` first attempts a clean
      ``turn/interrupt`` via :class:`AsyncTurnHandle` (when a turn is in
      flight), then closes the transport. The in-flight turn task lands in
      ``DEAD`` via :class:`TransportClosedError` either way.
    """

    provider: ClassVar[Provider] = Provider.CODEX

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        settings: AgentSettings,
        codex: AsyncCodex,
        thread: AsyncThread,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings=settings)
        self._codex = codex
        self._thread = thread
        # Tracked so ``interrupt_or_kill`` can fire ``turn/interrupt`` on the
        # active turn instead of yanking the whole transport.
        self._current_turn: AsyncTurnHandle | None = None
        self._turn_task: asyncio.Task[None] | None = None
        # ``reasoning`` items can fan out into several summary parts (each
        # with its own ``summaryIndex``). The SDK fires one
        # ``summaryPartAdded`` per part but a single ``item/completed`` for
        # the whole item, so we remember which indices we already started
        # streaming and emit a matching ``stream_block_stop`` + ``end`` for
        # each at completion time. Keyed by Codex ``item_id``.
        self._reasoning_summary_indices: dict[str, set[int]] = {}
        # Side-table for ``item/started`` payloads, indexed by ``itemId``.
        # Used to inject the diff into ``fileChange`` PendingRequests (the
        # approval payload itself doesn't carry it — see spec §1.1.b).
        # Populated on ``item/started``, popped on ``item/completed``,
        # cleared on ``interrupt_or_kill``.
        self._items_by_id: dict[str, dict] = {}
        # Map of itemId → human-readable reason for tools that the user
        # refused (Deny, Cancel turn, empty permissions grant). Codex's
        # ``function_call_output`` JSONL line has no ``is_error`` flag —
        # only an output string like "exec_command failed for ...
        # Rejected(...)" — so the Codex compute path
        # (``CodexSessionCompute.extract_tool_result_info``) consults this
        # side-table to know whether to mark the resulting
        # ``ToolResultLink`` as errored. See spec §1.1 + PR2c plan.
        # Lifetime: agent lifetime. Cleared by ``interrupt_or_kill`` (with
        # the rest of the side-tables) or by re-creating the agent on a
        # fresh session.
        self._denied_tool_ids: dict[str, str] = {}

        # Captured lazily in ``start()`` — that's the first place we're
        # guaranteed to be inside a running asyncio loop. The SDK's worker
        # threads dispatch approval callbacks back to this loop via
        # ``asyncio.run_coroutine_threadsafe``.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Capture the SDK's *default* sync approval handler BEFORE we
        # monkey-patch our own in. The default auto-accepts the 2 methods
        # it recognises and returns ``{}`` for others (see vendored
        # ``codex_app_server/client.py:480-485``). We delegate to it for
        # server requests we don't own (item/tool/call,
        # account/chatgptAuthTokens/refresh, …) — see spec §1.6, §7-Q9.
        # PRIVATE SDK API — see memory ``reference_codex_sdk_update_procedure.md``
        # for the upgrade checklist (this attribute path must hold).
        self._sdk_default_approval_handler = (
            self._codex._client._sync._approval_handler
        )
        # Replace the SDK's stub with our bridge. Must happen here, BEFORE
        # any ``thread_start`` / ``thread_resume`` runs (Codex could ship
        # the first approval immediately).
        self._codex._client._sync._approval_handler = self._sync_approval_handler

    @staticmethod
    def _sdk_effort(effort: str | None) -> ReasoningEffort | None:
        """Map our wire effort string to the SDK enum, ``None`` for unset/unknown.

        Unknown values fall through to ``None`` so Codex CLI picks its own
        default rather than crashing the turn — the dropdown only ever
        produces validated values today, this is a defensive guard.
        """
        if not effort:
            return None
        try:
            return ReasoningEffort(effort)
        except ValueError:
            logger.warning("Unknown Codex effort %r, falling back to CLI default", effort)
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        *,
        images: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Wire the state-change callback and schedule the first turn.

        ``images`` is the WS attachment payload (Claude-shaped image blocks)
        forwarded by the manager. ``documents`` is intentionally absent —
        Codex has no protocol for them, the manager drops them upstream
        with a warning.
        """
        self._state_change_callback = on_state_change

        # First place we're guaranteed to be inside a running loop. Captured
        # so the SDK's worker threads can resume our coroutines back here
        # via ``asyncio.run_coroutine_threadsafe`` (see ``_sync_approval_handler``).
        self._loop = asyncio.get_running_loop()

        # Flip to ASSISTANT_TURN immediately so the UI gates the input as
        # "working" — the actual turn runs in the background task below.
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text, images)

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Schedule a new turn on the live thread."""
        if self.state == AgentState.DEAD:
            raise RuntimeError("Cannot send message: agent is dead")
        if self.state == AgentState.ASSISTANT_TURN:
            raise RuntimeError(
                "Cannot send message: agent is busy (assistant turn in progress)",
            )

        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text, images)

    def _schedule_turn(self, text: str, images: list[dict] | None) -> None:
        """Spawn the background task that drives one turn end-to-end."""
        self._turn_task = asyncio.create_task(
            self._run_turn(text, images),
            name=f"codex-turn-{self.session_id}",
        )

    @staticmethod
    def _build_turn_input(
        text: str,
        images: list[dict] | None,
    ) -> list[InputItem]:
        """Convert the WS attachment payload to a Codex SDK ``Input`` list.

        Each WS image block is the Claude-shaped::

            {"type": "image",
             "source": {"type": "base64", "media_type": "image/...", "data": "..."}}

        Codex CLI's Rust core accepts ``ImageInput.url`` as either an
        http(s) URL or a base64 data URL — and even converts
        ``LocalImageInput(path)`` to the latter internally at request
        serialization time. We therefore re-pack the base64 + media_type
        pair into a single ``data:`` URL and let the SDK forward it
        verbatim. Blocks whose ``source.type`` is not ``"base64"`` are
        skipped defensively (the WS contract guarantees base64 today).

        Order: images first, then the text — mirrors Claude Code's
        content-block ordering so the two providers feel consistent when
        the user attaches references before phrasing the prompt.
        """
        items: list[InputItem] = []
        for block in images or ():
            source = block.get("source") or {}
            if source.get("type") != "base64":
                continue
            media_type = source.get("media_type") or "image/png"
            data = source.get("data") or ""
            if not data:
                continue
            items.append(ImageInput(url=f"data:{media_type};base64,{data}"))
        items.append(TextInput(text))
        return items

    async def _run_turn(self, text: str, images: list[dict] | None) -> None:
        """Open one turn, wait for it to complete, transition to USER_TURN.

        Errors raised by the SDK (transport closed, RPC errors, ...) are
        funnelled through ``_handle_error`` and surface as a ``DEAD`` state
        with an ``error`` message. ``TransportClosedError`` is treated as a
        clean shutdown when ``kill_reason`` is already set (i.e. the manager
        killed us on purpose) — no error toast in that case.
        """
        # ``Thread.turn`` expects an ``Input`` (TextInput/ImageInput/...) — only
        # ``Thread.run`` accepts a bare str via internal normalization. We don't
        # use ``run`` because it consumes the turn stream and hides the
        # ``TurnHandle`` we need for clean ``interrupt`` later on.
        #
        # ``effort`` and ``permission_mode`` are both read off
        # ``agent_settings`` per turn so live updates via ``send_to_session``
        # (which refreshes the bundle just before calling ``send``) take
        # effect on the next turn. ``effort=None`` lets Codex CLI use the
        # model's default (medium today). ``thread.turn(approval_policy=...,
        # sandbox_policy=...)`` accepts both as per-turn overrides — the
        # SDK forwards them as ``TurnStartParams`` on top of the values
        # bound at ``thread_start``, so the current turn keeps its policy
        # but the next one picks up the new picker value.
        effort = self._sdk_effort(self.agent_settings.effort)
        sandbox_policy, approval_policy = resolve_codex_turn_overrides(
            self.agent_settings.permission_mode,
        )
        turn_input = self._build_turn_input(text, images)
        try:
            turn_handle = await self._thread.turn(
                turn_input,
                effort=effort,
                approval_policy=approval_policy,
                sandbox_policy=sandbox_policy,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return

        self._current_turn = turn_handle

        # Consume the turn's notification stream ourselves (instead of the
        # blackbox ``turn_handle.run()``) so we can:
        #   - Broadcast ``stream_block_*`` WS events that paint the live
        #     assistant text in the frontend before the JSONL line lands.
        #   - Push each completed ``agentMessage`` item_id onto the FIFO
        #     registry so the watcher can stamp the matching SessionItem
        #     with ``stream_uuid`` and the frontend can retire the synthetic
        #     placeholder. (See ``streaming_registry.py`` for the why.)
        try:
            stream = turn_handle.stream()
            try:
                async for event in stream:
                    await self._handle_stream_event(event)
            finally:
                await stream.aclose()
        except TransportClosedError:
            # Manager closed the transport — expected during interrupt_or_kill
            # or shutdown. _handle_error already ran (or will run); avoid a
            # second transition if we're already DEAD.
            if self.state != AgentState.DEAD:
                self._set_state(AgentState.DEAD)
                self.last_activity = time.time()
                await self._notify_state_change()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Turn run failed: {e}", exc=e)
            return
        finally:
            self._current_turn = None

        # Turn completed normally → ready for the next user input.
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def interrupt_or_kill(self, reason: str) -> None:
        """Stop the agent. Tries a clean ``turn/interrupt`` first, then closes.

        Always lands in ``DEAD``. Safe to call multiple times.
        """
        if self.state == AgentState.DEAD:
            return

        self.kill_reason = reason

        # Cancel any in-flight approval BEFORE closing the transport.
        # Cascade per pending approval:
        #   future.cancel() → ``_await_pending_request`` raises CancelledError
        #                  → its ``finally`` clears the dict + broadcasts
        #                  → ``run_coroutine_threadsafe`` re-raises in the
        #                    SDK worker thread
        #                  → our ``_sync_approval_handler`` catches it and
        #                    returns ``default_response_for(method)``
        #                  → worker writes the wire response, releases
        #                    ``_transport_lock``
        # Now ``codex.close()`` can acquire the lock and tear down cleanly.
        # See spec §2.4 + §5.1.
        self._cancel_all_pending_futures()  # inherited from BaseAgent (PR1)

        # Clean turn cancellation when a turn is in flight. We don't gate this
        # on AgentState.ASSISTANT_TURN: depending on race timing, the turn
        # task may have just transitioned to USER_TURN but the next ``send``
        # could re-arm a turn before we observe DEAD. Issuing interrupt is a
        # best-effort no-op if no turn is active server-side.
        turn_handle = self._current_turn
        if turn_handle is not None:
            try:
                await turn_handle.interrupt()
            except Exception as e:
                logger.debug(
                    "turn_handle.interrupt() failed for session %s: %s — "
                    "falling back to transport close",
                    self.session_id, e,
                )

        # Close the codex transport — the turn task lands in DEAD via
        # TransportClosedError. Idempotent on the SDK side.
        try:
            await self._codex.close()
        except Exception as e:
            logger.warning(
                "codex.close() failed for session %s: %s", self.session_id, e,
            )

        # Cancel the turn task if it hasn't unwound from
        # TransportClosedError yet (e.g. it was awaiting something other
        # than ``turn_handle.run``).
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "Turn task raised on cancellation for session %s",
                    self.session_id, exc_info=True,
                )

        # Drop any item_ids buffered for this session. The agent is going
        # away, so the watcher will never get matching JSONL lines for
        # whatever was streamed and not yet completed (or whatever was
        # completed in the SDK after we tore the transport down). Keeping
        # them would corrupt the FIFO for the next agent on the same id.
        get_streamed_item_registry().clear_session(self.session_id)
        # Drop the side-table — no more turns will read it on this agent.
        self._items_by_id.clear()
        self._denied_tool_ids.clear()

        if self.state != AgentState.DEAD:
            self._set_state(AgentState.DEAD)
            self.last_activity = time.time()
            await self._notify_state_change()

    async def _handle_error(
        self, error_message: str, exc: Exception | None = None,
    ) -> None:
        """Surface a runtime error as a clean DEAD transition."""
        logger.error(
            "Codex agent for session %s died: %s",
            self.session_id, error_message,
            exc_info=exc,
        )
        self.error = error_message
        self.kill_reason = "error"
        try:
            await self._codex.close()
        except Exception:
            # Already broken — don't pile more errors on top.
            logger.debug(
                "codex.close() during error handling failed for session %s",
                self.session_id, exc_info=True,
            )
        get_streamed_item_registry().clear_session(self.session_id)
        self._set_state(AgentState.DEAD)
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # Stream event handling
    # ------------------------------------------------------------------

    async def _handle_stream_event(self, event: Any) -> None:
        """Translate one Codex SDK stream notification into TwiCC's WS protocol.

        We handle two item kinds today; everything else flows through the
        JSONL → watcher path. The mapping to the Claude-shared
        ``stream_block_*`` wire format is:

        - ``item/started`` on an ``agentMessage``
            → ``stream_block_start`` (``block_type="text"``, ``block_index=0``,
              ``message_id`` = Codex ``item_id``).
        - ``item/agentMessage/delta`` → ``stream_block_delta``.
        - ``item/completed`` on an ``agentMessage``
            → ``stream_block_stop`` + ``stream_block_end`` (``uuid`` =
              ``item_id``). Pushes the ``item_id`` onto the FIFO registry
              so the watcher can stamp the matching SessionItem.

        - ``item/reasoning/summaryPartAdded``
            → ``stream_block_start`` on the first summary part we see for
              this item (``block_type="thinking"``, ``block_index=0``).
              Subsequent summary parts emit a ``stream_block_delta`` with
              text ``"\\n\\n"`` instead, so the streaming view shows the
              same single concatenated reasoning card the JSONL line will
              render once flushed (the post-flush ``Reasoning.vue`` joins
              every ``summary_text`` with ``\\n\\n``). We deliberately
              ignore ``item/started`` on reasoning items because OpenAI
              sometimes returns an empty summary — we only want to paint
              a card when there's actual text to display, which is what
              ``summaryPartAdded`` signals.
        - ``item/reasoning/summaryTextDelta`` → ``stream_block_delta``
              (always on ``block_index=0`` — the summary_index of the
              specific part is hidden from the wire so the frontend sees
              one continuous block).
        - ``item/completed`` on a ``reasoning`` with non-empty summary
            → ``stream_block_stop`` + ``stream_block_end`` on
              ``block_index=0``, then a single registry push (the JSONL
              persists the whole reasoning as a single line, so a single
              pop on the watcher side will pair them).
        """
        # Refresh last_activity on every stream event so the
        # ASSISTANT_TURN inactivity timeout only fires on a truly silent
        # SDK (mirrors ClaudeCodeAgent._run_message_loop, where each
        # message coming out of the SDK touches last_activity).
        self.last_activity = time.time()

        method = event.method
        payload = event.payload

        if method == "item/started":
            # Capture the raw inner payload first so any ``itemId`` is indexed,
            # regardless of item kind. ``fileChange`` approvals later in the
            # turn read this side-table to grab the diff.
            item = getattr(payload, "item", None)
            if item is not None:
                inner = getattr(item, "root", item)
                item_id = getattr(inner, "id", None)
                if item_id:
                    self._items_by_id[item_id] = inner.model_dump(
                        mode="json", by_alias=True,
                    )

            # Existing agent-message streaming logic — only this kind paints
            # a live ``stream_block_start`` event today; other kinds flow
            # through the JSONL → watcher path.
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_start",
                "session_id": self.session_id,
                "message_id": agent_msg.id,
                "block_index": 0,
                "block_type": "text",
            })
            return

        if method == "item/agentMessage/delta":
            item_id = getattr(payload, "item_id", None)
            delta = getattr(payload, "delta", None)
            if not item_id or delta is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_delta",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "text",
                "text": delta,
            })
            return

        if method == "item/reasoning/summaryPartAdded":
            item_id = getattr(payload, "item_id", None)
            summary_index = getattr(payload, "summary_index", None)
            if not item_id or summary_index is None:
                return
            indices = self._reasoning_summary_indices.setdefault(item_id, set())
            if summary_index in indices:
                # Already started; the SDK shouldn't fire ``summaryPartAdded``
                # twice for the same (item_id, summary_index), but the guard
                # keeps us idempotent if it ever did.
                return
            first_part = not indices
            indices.add(summary_index)
            if first_part:
                await self._broadcast_stream_event({
                    "type": "stream_block_start",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                })
            else:
                # Subsequent summary part — paint a paragraph separator into
                # the same block instead of opening a new one, so the user
                # sees the same single Reasoning card the JSONL will render.
                await self._broadcast_stream_event({
                    "type": "stream_block_delta",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                    "text": "\n\n",
                })
            return

        if method == "item/reasoning/summaryTextDelta":
            item_id = getattr(payload, "item_id", None)
            delta = getattr(payload, "delta", None)
            if not item_id or delta is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_delta",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "thinking",
                "text": delta,
            })
            return

        if method == "item/completed":
            item = getattr(payload, "item", None)
            if item is None:
                return
            inner = getattr(item, "root", item)
            item_type = getattr(inner, "type", None)
            # The side-table entry is no longer needed once the item is
            # finalized (see ``_items_by_id`` in __init__). Pop is
            # idempotent — items we never saw started don't show up here.
            item_id_for_cleanup = getattr(inner, "id", None)
            if item_id_for_cleanup:
                self._items_by_id.pop(item_id_for_cleanup, None)

            if item_type == "agentMessage":
                item_id = inner.id
                await self._broadcast_stream_event({
                    "type": "stream_block_stop",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "text",
                })
                await self._broadcast_stream_event({
                    "type": "stream_block_end",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "text",
                    "uuid": item_id,
                })
                # Hand the item_id off to the watcher so it can stamp the
                # matching SessionItem when the JSONL line lands.
                get_streamed_item_registry().push(self.session_id, item_id)
                return

            if item_type == "reasoning":
                item_id = inner.id
                # If we never received a ``summaryPartAdded`` for this item
                # the set is empty (or absent) — typical when OpenAI didn't
                # produce a summary at all, in which case the JSONL line
                # carries ``summary: []`` and the watcher classifies it as
                # SYSTEM. No SessionItem to retire, no push needed.
                indices = self._reasoning_summary_indices.pop(item_id, set())
                if not indices:
                    return
                # Single block per reasoning item (block_index=0) regardless
                # of how many summary parts we saw — see ``summaryPartAdded``
                # above for the rationale.
                await self._broadcast_stream_event({
                    "type": "stream_block_stop",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                })
                await self._broadcast_stream_event({
                    "type": "stream_block_end",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                    "uuid": item_id,
                })
                # One JSONL line per reasoning item, so a single registry
                # push regardless of how many summary parts streamed.
                get_streamed_item_registry().push(self.session_id, item_id)
                return

    # ------------------------------------------------------------------
    # Approval handlers (sync ↔ async bridge)
    # ------------------------------------------------------------------

    def _sync_approval_handler(self, method: str, params: dict | None) -> dict:
        """Called by the SDK from a worker thread (via ``asyncio.to_thread``).

        Bridges the SDK's blocking expectation (``Callable -> dict``) to our
        async ``_await_pending_request``. Approvals we don't own (MCP, OAuth
        refresh, ...) delegate to the captured SDK default. Cancellation —
        typically from ``_cancel_all_pending_futures()`` on kill — is
        converted into a safe wire default so the SDK's read loop doesn't
        hang.

        See spec §2.4 + §5.1 for the full call chain.
        """
        if not is_approval_method(method):
            # Defensive fallback: log + delegate. The SDK default returns
            # ``{}`` for unknown methods which might break Codex; for the 2
            # approval methods it knows it returns ``{"decision": "accept"}``,
            # which is safer than crashing the read loop. PR2a does not
            # naturally exercise this path — the warning is here to flag
            # an unsupported server request the day it shows up.
            logger.warning(
                "Unhandled Codex server request method=%r (delegating to SDK default)",
                method,
            )
            return self._sdk_default_approval_handler(method, params)

        if self._loop is None or self._loop.is_closed():
            # Approval before ``start()`` ran, or after the loop was torn
            # down. Either way we can't bridge to async; return a safe
            # wire default so the SDK doesn't hang.
            logger.error(
                "Codex approval received before loop init or after close: method=%r",
                method,
            )
            return default_response_for(method)

        coro = self._async_approval_handler(method, params)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result()
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            # Pending future was cancelled (kill, transport teardown). The
            # awaiter's ``finally`` already dropped the entry; we just have
            # to give the SDK something to send back to Codex so the JSON-RPC
            # response is well-formed and the read loop unblocks.
            #
            # We catch BOTH classes because the asyncio coroutine raises
            # ``asyncio.CancelledError`` (a BaseException subclass since
            # Python 3.8) but ``run_coroutine_threadsafe(...).result()``
            # repackages it as ``concurrent.futures.CancelledError`` (an
            # Exception subclass) on the worker-thread side.
            return default_response_for(method)
        except Exception as exc:
            # Any other failure of the bridge — log loudly and fall back to
            # a safe default. Re-raising would leak the exception into the
            # SDK's worker thread which would then crash the entire read
            # loop.
            logger.error(
                "Codex approval bridge failed for method=%r: %s",
                method, exc, exc_info=True,
            )
            return default_response_for(method)

    async def _async_approval_handler(
        self, method: str, params: dict | None,
    ) -> dict:
        """Main-loop side of the bridge.

        Build a ``PendingRequest`` (enriched with the streamed item payload
        for ``fileChange``), broadcast it via ``_await_pending_request``,
        and return the dict the frontend sent back through
        ``manager.resolve_pending_request``.

        The WS layer is responsible for shape-validating the response into
        a Codex-compliant dict (``CodexWSHandler._build_codex_response``)
        — at this point we just pass it through.
        """
        item_id_for_log = params.get("itemId") if params else None
        logger.debug(
            "Codex approval request: session=%s method=%s itemId=%s",
            self.session_id, method, item_id_for_log,
        )
        enriched_params = self._enrich_params_with_item_payload(method, params)
        request = make_pending_request(method, enriched_params)
        response = await self._await_pending_request(request)
        # Record refusals in _denied_tool_ids so the Codex compute can
        # surface them as ToolResultLink.error when the matching
        # function_call_output lands in the JSONL.
        self._record_decision_outcome(method, params, response)
        return response

    def _enrich_params_with_item_payload(
        self, method: str, params: dict | None,
    ) -> dict | None:
        """For ``fileChange``, attach the streamed item payload (the diff).

        Other methods pass through unchanged. We do this BEFORE constructing
        the PendingRequest so ``tool_input`` carries the join data (under
        ``_item_payload``) and the frontend doesn't have to do a side fetch.

        The underscore prefix on ``_item_payload`` signals it's a synthetic
        side-band field, not from the Codex schema.
        """
        if method != "item/fileChange/requestApproval":
            return params
        if not params:
            return params
        item_id = params.get("itemId")
        if not item_id:
            return params
        payload = self._items_by_id.get(item_id)
        if payload is None:
            return params
        return {**params, "_item_payload": payload}

    # Item types from ``_items_by_id`` that produce a ``function_call_output``
    # in the JSONL (and therefore can be matched by ``_denied_tool_ids``).
    # We keep this set tight to avoid marking dead entries on cancel turn —
    # the lookup is harmless if we over-include, but the explicit list
    # documents which kinds we expect to surface as ``ToolResultLink``.
    # The SDK item-types stream as camelCase per ``model_dump(by_alias=True)``;
    # values here match what ``_items_by_id`` will hold.
    _CANCELLABLE_ITEM_TYPES: ClassVar[frozenset[str]] = frozenset({
        "commandExecution",
        "fileChange",
    })

    def _record_decision_outcome(
        self,
        method: str,
        params: dict | None,
        response: dict,
    ) -> None:
        """If the user refused the request, mark the matching itemId(s).

        Called from ``_async_approval_handler`` immediately after
        ``_await_pending_request`` returns. Three refusal shapes:

        - ``commandExecution`` / ``fileChange`` with ``decision == "decline"``:
          mark just the current itemId.
        - ``commandExecution`` / ``fileChange`` with ``decision == "cancel"``:
          mark the current itemId AND every in-flight item in
          ``_items_by_id`` whose type is in :attr:`_CANCELLABLE_ITEM_TYPES`
          (Codex will abort the whole turn — each in-flight tool gets
          an "aborted by user" output line).
        - ``permissions`` with empty granted profile:
          mark just the current itemId.

        ``response`` is the dict the frontend sent through
        ``resolve_pending_request``; ``params`` are the original Codex
        request params that contain ``itemId``. No-op if either is missing
        an itemId we can route from.
        """
        if not params:
            return
        item_id = params.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            return

        if method == "item/permissions/requestApproval":
            granted = response.get("permissions")
            if not granted:
                # Empty granted profile = user refused permissions.
                self._denied_tool_ids[item_id] = "Permissions denied by user"
                logger.debug(
                    "Codex decision recorded: session=%s itemId=%s "
                    "outcome=permissions_denied reason=%r",
                    self.session_id, item_id, "Permissions denied by user",
                )
            else:
                logger.debug(
                    "Codex decision recorded: session=%s itemId=%s "
                    "outcome=permissions_granted (no marking)",
                    self.session_id, item_id,
                )
            return

        # command / file
        decision = response.get("decision")
        if decision == "decline":
            self._denied_tool_ids[item_id] = "Denied by user"
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=decline reason=%r",
                self.session_id, item_id, "Denied by user",
            )
            return
        if decision == "cancel":
            self._denied_tool_ids[item_id] = "Turn cancelled by user"
            # Also mark every other in-flight function-call item. The user
            # asked for "tous les tools qui n'ont pas été terminés doivent
            # être marqués" — we iterate _items_by_id which holds every
            # item that emitted item/started but not item/completed yet.
            siblings_marked: list[str] = []
            for other_id, payload in self._items_by_id.items():
                if other_id == item_id:
                    continue
                if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
                    self._denied_tool_ids[other_id] = "Turn cancelled by user"
                    siblings_marked.append(other_id)
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=cancel reason=%r siblings_marked=%s",
                self.session_id, item_id,
                "Turn cancelled by user", siblings_marked,
            )
            return
        # Anything else (notably "approve" on command/file) is a pass-through
        # with no map entry — trace it so the smoke-test grep shows the
        # full approve/deny picture for each itemId.
        logger.debug(
            "Codex decision recorded: session=%s itemId=%s "
            "outcome=%s (no marking)",
            self.session_id, item_id, decision,
        )

    async def _broadcast_stream_event(self, data: dict[str, Any]) -> None:
        """Broadcast a streaming event to all connected WebSocket clients.

        Mirror of ``ClaudeCodeAgent._broadcast_stream_event``: pushes through
        the ``"updates"`` channel group; the consumer's ``broadcast`` handler
        forwards ``data`` verbatim as a WS message.
        """
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": data},
        )
