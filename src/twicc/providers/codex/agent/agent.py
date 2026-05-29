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
import re
import time
from pathlib import Path
from typing import Any, ClassVar

from channels.layers import get_channel_layer

from openai_codex import (
    AsyncTurnHandle,
    ImageInput,
    InputItem,
    TextInput,
    TransportClosedError,
)
from openai_codex.generated.v2_all import (
    CodexErrorInfoValue,
    ErrorNotification,
    HttpConnectionFailedCodexErrorInfo,
    ReasoningEffort,
    ResponseStreamConnectionFailedCodexErrorInfo,
)

from twicc.agent import AgentState, BaseAgent, StateChangeCallback
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from ..permission_modes import resolve_codex_turn_overrides
from ..sdk_wrappers import TwiccAsyncCodex, TwiccAsyncThread
from ..streaming_registry import get_streamed_item_registry
from .approvals import (
    default_response_for,
    is_approval_method,
    make_pending_request,
)
from .original_files_cache import (
    MAX_FILE_SIZE as _ORIGINAL_FILE_MAX_SIZE,
    cache_original_files,
    clear_session as clear_original_files_for_session,
)
from .sdk_logger import log_approval_request, log_approval_response, log_stream_event

logger = logging.getLogger(__name__)

# Pattern for HTTP 401/403 in a Codex terminal error message. Codex upstream
# does not map ``CodexErr::UnexpectedStatus(401)`` to ``CodexErrorInfo::Unauthorized``
# — it falls through to ``Other`` and the only auth signal left is the
# formatted message (``"unexpected status 401 Unauthorized: ..."``). Used by
# ``CodexAgent._is_unauthorized_error`` as the third detection path.
_AUTH_STATUS_IN_MESSAGE = re.compile(r"\bstatus\s+40[13]\b", re.IGNORECASE)


def _capture_original_files_for_apply_patch(inner: Any, session_id: str) -> None:
    """Read each file targeted by an ``apply_patch`` and store the contents.

    Called from ``CodexAgent._handle_stream_event`` when a
    ``FileChangeThreadItem`` is announced via ``item/started`` — that
    notification fires before the Codex CLI subprocess applies the patch,
    so the on-disk content here is the pre-patch original. Sync read (no
    ``await``) is intentional: in ``yolo`` mode (``approval_policy="never"``)
    the patch is applied a few ms later, and yielding to the event loop
    risks losing that window. See ``original_files_cache.py`` for the
    full rationale.

    Files that don't exist on disk (``add`` kind) are silently skipped —
    nothing to capture. Files larger than ``MAX_FILE_SIZE`` are skipped
    too, matching Claude's per-file limit.
    """
    item_id = getattr(inner, "id", None)
    if not isinstance(item_id, str) or not item_id:
        return

    changes = getattr(inner, "changes", None) or ()
    captured: dict[str, str] = {}
    for change in changes:
        path_str = getattr(change, "path", None)
        if not isinstance(path_str, str) or not path_str:
            continue
        try:
            path = Path(path_str)
            if not path.is_file():
                # New file (add), rename destination, or any other case
                # where there's no pre-patch content on disk.
                continue
            if path.stat().st_size > _ORIGINAL_FILE_MAX_SIZE:
                continue
            content = path.read_text(encoding="utf-8")
        except Exception:
            logger.debug(
                "Failed to capture original file %s for call %s",
                path_str, item_id, exc_info=True,
            )
            continue
        captured[path_str] = content

    cache_original_files(session_id, item_id, captured)


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
    - ``ASSISTANT_TURN`` → ``ASSISTANT_TURN``: ``send(text)`` steers the
      active turn — the input is injected via ``turn/steer`` and consumed
      by Codex at the next turn cycle. No state transition.
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
        codex: TwiccAsyncCodex,
        thread: TwiccAsyncThread,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings=settings)
        self._codex = codex
        self._thread = thread
        # Tracked so ``interrupt_or_kill`` can fire ``turn/interrupt`` on the
        # active turn instead of yanking the whole transport.
        self._current_turn: AsyncTurnHandle | None = None
        # Set in ``_run_turn`` once the ``thread.turn`` RPC roundtrip has
        # returned and ``_current_turn`` is published; cleared when the turn
        # unwinds. ``send()`` awaits this when steering so a fast second
        # message doesn't race the turn handshake and find ``_current_turn``
        # still ``None`` despite ``state == ASSISTANT_TURN``.
        self._current_turn_ready: asyncio.Event = asyncio.Event()
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
        # ``openai_codex/client.py``). We delegate to it for
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
        """Schedule a new turn, or steer the active one.

        - ``USER_TURN``: schedule a fresh turn (the normal flow).
        - ``ASSISTANT_TURN``: steer — push the input into the active
          ``TurnHandle`` so Codex picks it up at the next turn cycle, without
          interrupting tool execution or reasoning. The SDK's ``turn_steer``
          serializes behind the async ``_transport_lock`` held by the
          concurrent ``stream()`` loop, so the steer lands at the next
          notification boundary (sub-second during streaming, seconds during
          a silent tool execution).
        - ``DEAD``: refuse.

        ``turn/steer`` carries only the input — model / effort / sandbox /
        approval overrides are NOT applied to the active turn. Settings
        changed during ``ASSISTANT_TURN`` are refreshed on the agent by the
        manager and pick up on the NEXT ``_run_turn``.
        """
        if self.state == AgentState.DEAD:
            raise RuntimeError("Cannot send message: agent is dead")

        if self.state == AgentState.ASSISTANT_TURN:
            # ``_run_turn`` publishes ``_current_turn`` only after the
            # ``thread.turn`` RPC returns. A fast steer could race that
            # window; bound the wait so a broken handshake doesn't hang
            # the WS request.
            try:
                await asyncio.wait_for(
                    self._current_turn_ready.wait(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError as e:
                raise RuntimeError(
                    "Cannot steer: turn handshake did not complete in time",
                ) from e

            turn_handle = self._current_turn
            if turn_handle is None:
                # Ready was set then cleared between wait and read — turn
                # ended. Caller can retry on the next state-change event.
                raise RuntimeError(
                    "Cannot steer: turn ended before steer could be issued",
                )

            turn_input = self._build_turn_input(text, images)
            try:
                await turn_handle.steer(turn_input)
            except TransportClosedError:
                raise
            except Exception as e:
                logger.warning(
                    "Codex steer failed for session %s: %s",
                    self.session_id, e,
                )
                raise RuntimeError(f"Steer failed: {e}") from e

            self.last_activity = time.time()
            return

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
        # ``effort``, ``permission_mode`` and ``selected_model`` are all
        # read off ``agent_settings`` per turn so live updates via
        # ``send_to_session`` (which refreshes the bundle just before
        # calling ``send``) take effect on the next turn. ``effort=None``
        # / ``model=None`` lets Codex CLI use its own default. The SDK
        # accepts ``approval_policy``, ``sandbox_policy``, ``effort`` and
        # ``model`` as per-turn overrides on ``thread.turn`` — they're
        # forwarded as ``TurnStartParams`` on top of the values bound at
        # ``thread_start``, so the current turn keeps its policy but the
        # next one picks up the new picker value.
        effort = self._sdk_effort(self.agent_settings.effort)
        sandbox_policy, approval_policy = resolve_codex_turn_overrides(
            self.agent_settings.permission_mode,
        )
        sdk_model = get_provider_helpers(Provider.CODEX).resolve_sdk_model(
            self.agent_settings.selected_model,
        )
        turn_input = self._build_turn_input(text, images)
        try:
            turn_handle = await self._thread.turn_with_policy(
                turn_input,
                model=sdk_model,
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
        self._current_turn_ready.set()

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
                self.last_activity = time.time()
                await self._transition_to_dead()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Turn run failed: {e}", exc=e)
            return
        finally:
            self._current_turn = None
            self._current_turn_ready.clear()

        # Skip the USER_TURN transition if an in-stream branch already
        # moved us to DEAD (e.g. terminal ``error`` notification). The
        # stream loop above can exit cleanly after that — via a final
        # ``turn/completed`` arriving before ``self._codex.close()``
        # propagates — and we don't want to overwrite the terminal state.
        if self.state == AgentState.DEAD:
            return

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
        # Drop any captured pre-patch contents that won't be consumed
        # (the matching ``patch_apply_end`` won't be emitted on a torn-down
        # transport). The TTL would clean them up eventually; this just
        # makes the boundary explicit.
        clear_original_files_for_session(self.session_id)

        if self.state != AgentState.DEAD:
            self.last_activity = time.time()
            await self._transition_to_dead()

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
        self.last_activity = time.time()
        await self._transition_to_dead()

    # ------------------------------------------------------------------
    # Process introspection
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        """Get the PID of the underlying Codex CLI subprocess.

        ``AsyncCodex._client._sync._proc`` is the :class:`subprocess.Popen`
        wrapping the bundled Rust ``codex app-server`` binary. ``None``
        before ``codex.start()`` runs and again once ``close()`` clears it.

        PRIVATE SDK API — see memory ``reference_codex_sdk_update_procedure.md``
        for the upgrade checklist (this attribute path must hold).
        """
        try:
            client = getattr(self._codex, "_client", None)
            if client is None:
                return None
            sync_client = getattr(client, "_sync", None)
            if sync_client is None:
                return None
            proc = getattr(sync_client, "_proc", None)
            if proc is None:
                return None
            return getattr(proc, "pid", None)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Stream event handling
    # ------------------------------------------------------------------

    @staticmethod
    def _is_unauthorized_error(payload: ErrorNotification) -> bool:
        """Return ``True`` when this terminal error looks like an auth failure.

        Three paths can surface an auth failure depending on where Codex
        catches the 401/403:

        1. ``CodexErrorInfoValue.unauthorized`` — Codex mapped
           ``CodexErr::RefreshTokenFailed`` (token refresh attempted and
           failed permanently). Only reachable when TwiCC handles the
           ``account/chatgptAuthTokens/refresh`` server request — not
           wired today, so this path doesn't fire in practice.
        2. ``HttpConnectionFailedCodexErrorInfo`` /
           ``ResponseStreamConnectionFailedCodexErrorInfo`` with
           ``http_status_code in {401, 403}`` — Codex classified the
           network error and exposes the status directly.
        3. ``"status 40[13]"`` in the message — covers the common
           ``CodexErr::UnexpectedStatus(401)`` case (e.g. session resume
           on an expired token). Codex upstream has no dedicated mapping
           for ``UnexpectedStatus`` in ``to_codex_protocol_error``, so it
           falls through to ``Other`` and the HTTP status is only visible
           in the formatted message (``"unexpected status {status}: ..."``).

        A false positive flips the topbar red briefly — the next
        ``codex login status`` poll (≤30s) rectifies it. A false negative
        means the user keeps seeing a green topbar while every request
        fails, which is worse, so we prefer being a touch aggressive.
        """
        info = payload.error.codex_error_info
        if info is not None:
            root = info.root
            if root is CodexErrorInfoValue.unauthorized:
                return True
            if isinstance(root, HttpConnectionFailedCodexErrorInfo):
                if root.http_connection_failed.http_status_code in (401, 403):
                    return True
            elif isinstance(root, ResponseStreamConnectionFailedCodexErrorInfo):
                if root.response_stream_connection_failed.http_status_code in (401, 403):
                    return True
        return bool(_AUTH_STATUS_IN_MESSAGE.search(payload.error.message))

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
        # Mirror the raw SDK notification into the per-session debug log
        # before any local processing. No-op when TWICC_DEBUG is unset.
        log_stream_event(self.session_id, event)

        # Refresh last_activity on every stream event so the
        # ASSISTANT_TURN inactivity timeout only fires on a truly silent
        # SDK (mirrors ClaudeCodeAgent._run_message_loop, where each
        # message coming out of the SDK touches last_activity).
        self.last_activity = time.time()

        method = event.method
        payload = event.payload

        # Subagent traffic filter: Codex routes every notification from
        # a spawned subagent through the parent's SDK transport (single
        # Rust process, single notification stream). Each notification
        # carries its origin ``thread_id`` — for subagent items it
        # differs from ``self.session_id``. We must drop those events
        # here for two reasons:
        #
        #   1. ``stream_block_*`` broadcasts go out tagged with
        #      ``self.session_id``, so painting the subagent's text
        #      into the parent conversation would surface content the
        #      user already sees through the spawn_agent tool card +
        #      the dedicated subagent tab.
        #   2. The :class:`StreamedItemRegistry` FIFO is keyed by
        #      ``self.session_id``. A subagent push would consume the
        #      slot meant for the parent's next ``agent_message``,
        #      leaving the streaming placeholder stuck — the
        #      ``stream_uuid`` stamped on the parent's SessionItem ends
        #      up being a subagent item id the frontend never painted a
        #      placeholder for, so retirement-by-uuid never matches.
        #
        # Notifications without a ``thread_id`` (none today, but keep
        # the guard defensive against future SDK additions) flow
        # through unchanged.
        payload_thread_id = getattr(payload, "thread_id", None)
        if payload_thread_id is not None and payload_thread_id != self.session_id:
            return

        if method == "error" and isinstance(payload, ErrorNotification):
            # ``EventMsg::Error`` upstream → ``will_retry: false`` and a
            # ``turn/completed`` with ``status: failed`` follows on the same
            # stream. ``EventMsg::StreamError`` → ``will_retry: true`` and is
            # a transient SSE retry the SDK handles on its own — don't kill
            # the agent or flip auth state on those.
            if payload.will_retry:
                return

            is_auth_error = self._is_unauthorized_error(payload)
            if is_auth_error:
                logger.error(
                    "Codex auth error for session %s: %s",
                    self.session_id, payload.error.message,
                )
            else:
                logger.error(
                    "Codex terminal error for session %s: %s (codex_error_info=%r)",
                    self.session_id,
                    payload.error.message,
                    payload.error.codex_error_info,
                )

            # Mirror Claude's order of ops on ``authentication_failed``:
            # cancel pending futures → set DEAD → notify → (auth only:
            # flip global auth state) → close transport. The flip happens
            # after the DEAD broadcast so the frontend's topbar reflects
            # the new auth state without waiting for the next
            # ``codex login status`` poll (up to 30s away).
            self._cancel_all_pending_futures()
            self.error = payload.error.message
            self.kill_reason = "auth_required" if is_auth_error else "error"
            self.last_activity = time.time()
            await self._transition_to_dead()

            if is_auth_error:
                from twicc.providers.codex.auth import mark_unauthenticated_and_broadcast
                await mark_unauthenticated_and_broadcast()

            # Drop side-tables tied to this session so a future agent on
            # the same id starts clean (same cleanup as ``interrupt_or_kill``).
            get_streamed_item_registry().clear_session(self.session_id)
            self._items_by_id.clear()
            self._denied_tool_ids.clear()
            clear_original_files_for_session(self.session_id)

            # Tear the transport down. The stream loop in ``_run_turn``
            # will observe ``TransportClosedError`` next and exit; the
            # DEAD guard there (and at the post-loop tail) prevents the
            # state from being overwritten by USER_TURN.
            try:
                await self._codex.close()
            except Exception:
                logger.debug(
                    "codex.close() after error notification failed for session %s",
                    self.session_id, exc_info=True,
                )
            return

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
                # ``fileChange`` items announce an upcoming ``apply_patch``.
                # Read the pre-patch contents synchronously so the watcher
                # can splice them into the persisted ``patch_apply_end``
                # for full-file diffs on the frontend. See
                # :func:`_capture_original_files_for_apply_patch` for the
                # timing rationale.
                if getattr(inner, "type", None) == "fileChange":
                    _capture_original_files_for_apply_patch(inner, self.session_id)

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
        """Logging wrapper around the actual approval bridge.

        Records the inbound SDK request + the response we hand back, then
        delegates to :meth:`_sync_approval_handler_impl` for the real
        bridge logic. Both ``log_*`` calls are no-ops when TWICC_DEBUG is
        unset, so production keeps the original code path overhead.

        Wrapping at this layer (rather than instrumenting each return
        inside the impl) guarantees we capture every response — including
        the safe defaults returned on cancellation, missing event loop,
        bridge crash, etc. Those failure-mode responses are exactly what
        debug logs need to surface.
        """
        log_approval_request(self.session_id, method, params)
        response = self._sync_approval_handler_impl(method, params)
        log_approval_response(self.session_id, method, response)
        return response

    def _sync_approval_handler_impl(self, method: str, params: dict | None) -> dict:
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
                self._denied_tool_ids[item_id] = "User refused permissions"
                logger.debug(
                    "Codex decision recorded: session=%s itemId=%s "
                    "outcome=permissions_denied reason=%r",
                    self.session_id, item_id, "User refused permissions",
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
            self._denied_tool_ids[item_id] = "User denied this action"
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=decline reason=%r",
                self.session_id, item_id, "User denied this action",
            )
            return
        if decision == "cancel":
            self._denied_tool_ids[item_id] = "User cancelled this turn"
            # Also mark every other in-flight function-call item. The user
            # asked for "tous les tools qui n'ont pas été terminés doivent
            # être marqués" — we iterate _items_by_id which holds every
            # item that emitted item/started but not item/completed yet.
            siblings_marked: list[str] = []
            for other_id, payload in self._items_by_id.items():
                if other_id == item_id:
                    continue
                if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
                    self._denied_tool_ids[other_id] = "User cancelled this turn"
                    siblings_marked.append(other_id)
                    logger.debug(
                        "Codex cancel: marking sibling session=%s itemId=%r type=%r",
                        self.session_id, other_id, payload.get("type"),
                    )
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=cancel reason=%r siblings_marked=%s",
                self.session_id, item_id,
                "User cancelled this turn", siblings_marked,
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
