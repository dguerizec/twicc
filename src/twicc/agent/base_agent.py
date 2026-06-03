"""
Base class for a running agent instance.

A ``BaseAgent`` represents one live instance of a coding-agent runtime
(Claude Code SDK, Codex, ...) for a single TwiCC session. Subclasses provide
the runtime-specific lifecycle (``start``/``send``/``interrupt_or_kill``); the
base owns the state machine, lifecycle timestamps, and process introspection
shared by every provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar

from twicc.context_injection import clear_context
from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context

from .states import AgentInfo, AgentState, PendingRequest, get_process_memory

if TYPE_CHECKING:
    from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# Async callback invoked when the agent transitions between states.
StateChangeCallback = Callable[["BaseAgent"], Coroutine[Any, Any, None]]


class BaseAgent:
    """Skeleton for a single agent instance.

    Subclasses must implement ``start``, ``send`` and ``interrupt_or_kill``.
    They typically also override ``get_info`` to add provider-specific fields
    on top of the base snapshot (via ``AgentInfo._replace``).

    Subclasses must also set the ``provider`` class attribute to the
    provider key registered in ``AgentManagerRegistry.PROVIDER_MANAGERS``.
    """

    # Provider key (e.g. ``Provider.CLAUDE_CODE``). Subclasses must override.
    provider: ClassVar[Provider]

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        agent_settings: AgentSettings,
    ) -> None:
        # Fail fast if the subclass forgot to set its provider key. Without
        # this guard, the missing attribute would only surface deep inside
        # the first ``get_info()`` call as an opaque ``AttributeError``.
        if not getattr(type(self), "provider", None):
            raise TypeError(
                f"{type(self).__name__}.provider must be set to a "
                "Provider enum class attribute (e.g. Provider.CLAUDE_CODE)."
            )

        self.session_id = session_id
        self.project_id = project_id
        self.cwd = cwd

        # Per-session agent settings as a single typed bundle. Mutate via
        # ``_replace`` so the assignment site is the only place a setting
        # changes (no scattered ``self.permission_mode = ...`` writes).
        self.agent_settings = agent_settings

        self.state: AgentState = AgentState.STARTING
        self.previous_state: AgentState | None = None
        self.started_at = time.time()
        self.state_changed_at = self.started_at
        self.last_activity = self.started_at
        self.error: str | None = None
        self.kill_reason: str | None = None

        self._dead_event = asyncio.Event()
        # Set by ``_transition_to_dead`` after the DEAD state-change callback
        # finishes (success or exception). ``wait_for_dead`` blocks on this
        # event in addition to ``_dead_event`` so callers that need a
        # fully-stopped agent — most importantly ``BaseAgentManager.shutdown``,
        # which runs immediately before ``stop_db_writer()`` in the server
        # shutdown sequence — only proceed once any DB writes performed in
        # the DEAD callback under ``run_under_db_write_lock`` have committed.
        self._dead_callback_done_event = asyncio.Event()
        self._state_change_callback: StateChangeCallback | None = None

        # Pending requests waiting on a user click (tool approval, ask user
        # question, …). Keyed by request_id (UUID). Provider subclasses populate
        # these via ``_await_pending_request``; the WS layer consumes them via
        # ``resolve_pending_request`` and the manager-level
        # ``BaseAgentManager.resolve_pending_request``.
        # ``_pending_futures`` is typed ``Any`` because each provider's SDK
        # returns its own decision type (Claude: PermissionResult{Allow,Deny};
        # Codex: raw dict). The caller is responsible for the cast.
        self._pending_requests: dict[str, PendingRequest] = {}
        self._pending_futures: dict[str, asyncio.Future[Any]] = {}

        # ProcessRun model row, populated by the manager once the agent is registered.
        self.process_run: Any = None

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_state(self, new_state: AgentState) -> None:
        """Transition into ``new_state`` and trigger the DEAD-event side-effect."""
        old_state = self.state
        self.previous_state = old_state
        self.state = new_state
        self.state_changed_at = time.time()
        if new_state == AgentState.DEAD:
            self._dead_event.set()
        with provider_log_context(self.provider):
            logger.debug(
                "State transition for session %s: %s -> %s",
                self.session_id, old_state.value, new_state.value,
            )

    async def _notify_state_change(self) -> None:
        """Dispatch the registered state-change callback, swallowing failures.

        Wrapped in :func:`provider_log_context` so every log emitted by the
        callback chain (typically ``BaseAgentManager._on_state_change`` and
        its descendants) carries the provider tag — agent message loops may
        run in tasks that were not created via the orchestrator's
        ``_create_task`` helper (e.g. tasks reached from a WS consumer),
        so the tag must be (re-)posted here to cover the whole notify path.
        """
        if self._state_change_callback is None:
            return
        with provider_log_context(self.provider):
            try:
                await self._state_change_callback(self)
            except Exception as e:
                logger.error(
                    "Error in state change callback for session %s: %s",
                    self.session_id, e, exc_info=True,
                )

    async def _transition_to_dead(self) -> None:
        """Atomically transition into DEAD: set state, notify, then signal done.

        Callers must set the agent-side attributes that reflect the final
        state (``kill_reason``, ``error``, ``last_activity``, provider-specific
        events like ``_first_turn_done_event``) BEFORE invoking this helper —
        those must be visible to the state-change callback. The helper owns
        the three steps that must not be split: ``_set_state(DEAD)``, the
        awaited ``_notify_state_change``, and the
        ``_dead_callback_done_event`` signal that releases ``wait_for_dead``.

        Keeping these three steps in one helper is what closes the shutdown
        race exposed by the agent-lifecycle wiring: ``BaseAgentManager.shutdown``
        gathers ``wait_for_dead`` on every agent before returning, so
        ``stop_db_writer`` cannot fire while a DEAD callback's
        ``run_under_db_write_lock`` acquire is still queued. The same
        guarantee protects every non-shutdown caller that polls
        ``wait_for_dead`` (cron restart, settings-triggered restart).

        Cancellation safety: the notify runs on a separate task we
        shield-loop on. Without the shield, an outer cancel (e.g.
        ``BaseAgentManager.shutdown`` cancelling a pending
        ``interrupt_or_kill`` Task on timeout, or ``kill()`` cancelling the
        message loop that is mid-``_transition_to_dead``) would propagate
        through ``await self._notify_state_change()`` BEFORE the callback's
        ``run_under_db_write_lock`` acquire actually applies its DB writes —
        and the ``finally`` would still fire the done event, unblocking
        ``wait_for_dead`` while the writes were never queued. The shield-loop
        holds the done-event signal back until the notify task is genuinely
        finished; any outer cancellation we caught is re-raised once the
        callback has run to completion.
        """
        # Drop any context injection this session queued but never consumed (an
        # agent that died before composing its first user message). Generic
        # across providers; a pure dict pop with no await, safe to run before
        # the state-transition dance below. See :mod:`twicc.context_injection`.
        clear_context(self.session_id)
        self._set_state(AgentState.DEAD)
        notify_task = asyncio.create_task(
            self._notify_state_change(),
            name=f"dead-notify-{self.session_id}",
        )
        captured_outer_cancel: asyncio.CancelledError | None = None
        current = asyncio.current_task()
        # Watermark of pending outer cancellations observed so far.
        # ``Task.cancelling()`` is sticky (only ``uncancel()`` decrements
        # it), so a level check ``cancelling() > 0`` would keep matching
        # forever once any outer cancel arrived — including for a later
        # inner self-cancel propagating through the shield. The
        # watermark lets us identify a NEW outer cancel (count went up)
        # vs the same one we already saw (count unchanged).
        cancelling_watermark = current.cancelling() if current is not None else 0
        try:
            while not notify_task.done():
                try:
                    await asyncio.shield(notify_task)
                except asyncio.CancelledError as exc:
                    # ``asyncio.shield`` raises ``CancelledError`` for two
                    # distinct sources:
                    #   * OUR task was cancelled (outer cancel).
                    #   * ``notify_task`` itself was cancelled (inner
                    #     self-cancel — e.g. someone called
                    #     ``notify_task.cancel()`` directly, or the
                    #     callback awaited an already-cancelled future).
                    # Discriminate via two complementary signals:
                    #   (a) ``cancelling()`` advanced past the watermark —
                    #       a new outer cancel was injected at this await.
                    #   (b) ``notify_task`` is still NOT done — the shield
                    #       only raises ``CancelledError`` while the inner
                    #       is alive when the source is an outer cancel.
                    #       Catches the pre-entry-pending edge: caller had
                    #       ``cancel()`` requested but no await had
                    #       consumed it yet, so ``cancelling()`` started
                    #       above zero and didn't move when the delivery
                    #       finally happened at our first shield await.
                    # Either signal classifies the catch as outer; we keep
                    # the FIRST outer cancel so multi-outer-cancel doesn't
                    # overwrite the original message/cause/traceback.
                    # Inner cancellations surface via the drain below as
                    # the authoritative ones.
                    is_outer = False
                    if current is not None:
                        latest = current.cancelling()
                        if latest > cancelling_watermark:
                            cancelling_watermark = latest
                            is_outer = True
                    if not is_outer and not notify_task.done():
                        is_outer = True
                    if is_outer and captured_outer_cancel is None:
                        captured_outer_cancel = exc
                    continue
                except Exception:
                    # Inner raised — ``notify_task.done()`` is True so the
                    # next iteration exits the loop. The drain block below
                    # surfaces the exception under the precedence rule.
                    pass
        finally:
            # ``notify_task.done()`` is now True — either via the while
            # loop or because an eager task factory (Python 3.12+) ran
            # the coro synchronously at ``create_task`` and the very
            # first check saw the task already done. Signal
            # ``wait_for_dead()`` only after the inner is genuinely
            # terminal.
            self._dead_callback_done_event.set()
        # If we entered the helper with an outer cancel already pending
        # (``cancelling() > 0`` at entry) AND the eager task factory
        # short-circuited the shield-loop (``notify_task`` was done
        # before the first ``done()`` check), the pending outer cancel
        # was never delivered to an await — so neither the watermark
        # nor the ``notify_task.done()`` discriminator above could
        # capture it. Force a delivery via a zero-delay yield: asyncio
        # injects the pending ``CancelledError`` on the next await,
        # which is our ``sleep(0)``. Without this, the drain below
        # would propagate the inner cancellation (or return normally),
        # silently losing the outer cancel that should take precedence.
        if (
            captured_outer_cancel is None
            and current is not None
            and current.cancelling() > 0
        ):
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError as exc:
                captured_outer_cancel = exc
        # Drain ``notify_task``'s outcome. The shield-loop above exits as
        # soon as ``notify_task.done()`` is True, but two edge cases let
        # it exit without anyone reading the inner outcome:
        #   * Eager task factory (Python 3.12+): the coro may run to
        #     completion synchronously at ``create_task``, so the very
        #     first ``done()`` check passes and the loop body never
        #     awaits. Without this drain, both a synchronous exception
        #     AND a synchronous ``CancelledError`` from the inner would
        #     be silently swallowed.
        #   * Same-tick race: an outer cancel arriving on the same tick
        #     as inner completion exits the loop via ``done()`` without
        #     re-awaiting, missing the inner exception.
        # ``task.result()`` raises whatever the inner produced
        # (``CancelledError`` for a cancelled task, the actual exception
        # for a failed one) and returns the value otherwise; that's the
        # cleanest way to consume both kinds of outcome.
        # Precedence rule:
        #   * Outer cancellation always wins (the caller's tear-down
        #     contract is "this is being torn down"). Inner exceptions
        #     are logged for observability; an inner cancellation is
        #     suppressed silently (no signal in a redundant cancel).
        #   * Without an outer cancel, the inner result is propagated
        #     verbatim — including a synchronous ``CancelledError`` from
        #     the eager-task-factory path, which would otherwise be
        #     lost.
        try:
            notify_task.result()
        except asyncio.CancelledError:
            if captured_outer_cancel is None:
                # Inner cancelled with no outer cancel — propagate the
                # inner cancellation, preserving its message/cause via a
                # bare ``raise``.
                raise
            # Outer cancel takes precedence; inner cancel is redundant.
        except Exception as inner_exc:
            if captured_outer_cancel is not None:
                with provider_log_context(self.provider):
                    logger.error(
                        "DEAD callback for session %s raised while the "
                        "transition was being cancelled; suppressing inner: %s",
                        self.session_id, inner_exc, exc_info=inner_exc,
                    )
            else:
                raise
        if captured_outer_cancel is not None:
            raise captured_outer_cancel

    async def wait_for_dead(self, timeout: float = 30.0) -> bool:
        """Wait until the agent is fully torn down: DEAD state AND callback done.

        Returns ``True`` when both events fire within ``timeout``, ``False``
        on timeout. ``timeout`` covers the whole wait, not each stage. The
        two-stage wait is what makes the manager shutdown safe against the
        DB writer being stopped before a DEAD callback's lock-protected
        writes commit; see :meth:`_transition_to_dead` for the rationale.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            await asyncio.wait_for(self._dead_event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        remaining = max(0.0, deadline - asyncio.get_event_loop().time())
        try:
            await asyncio.wait_for(
                self._dead_callback_done_event.wait(), remaining,
            )
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------
    # Pending requests (shared by every provider)
    # ------------------------------------------------------------------

    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        """Active pending requests waiting for user response, oldest first."""
        return tuple(
            sorted(self._pending_requests.values(), key=lambda r: r.created_at)
        )

    async def _await_pending_request(self, request: PendingRequest) -> Any:
        """Register a pending request, broadcast, wait for resolution, return raw response.

        Provider subclasses construct the ``PendingRequest`` (which knows the
        provider-specific ``tool_name`` / ``tool_input`` / suggestions) and
        delegate the bookkeeping here. The Future's ``set_result`` is invoked
        by ``resolve_pending_request`` when the WS layer routes a user decision
        back, or via ``_cancel_all_pending_futures`` on kill.

        The return type is ``Any`` because each provider's wire decision is its
        own type — the caller in the subclass casts.
        """
        self._pending_requests[request.request_id] = request
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_futures[request.request_id] = future

        # Tell the frontend a new pending request is in flight.
        await self._notify_state_change()

        try:
            return await future
        finally:
            # Drop the entry whether we resolved or were cancelled.
            self._pending_requests.pop(request.request_id, None)
            self._pending_futures.pop(request.request_id, None)
            # If the agent has already transitioned to DEAD (typically because
            # ``interrupt_or_kill`` cancelled this pending future as part of
            # its cleanup), the DEAD state-change callback has already fired
            # — or is in flight — via ``_transition_to_dead``. Re-invoking
            # ``_notify_state_change`` here would run an untracked second
            # copy of the DEAD callback whose DB writes are NOT covered by
            # ``_dead_callback_done_event``, so ``BaseAgentManager.shutdown``'s
            # ``wait_for_dead`` drain would not block on them and
            # ``stop_db_writer`` could race the queued lock acquire. Skip
            # the cleanup broadcast in that case — the DEAD broadcast
            # already announced the final state to the frontend.
            if self.state != AgentState.DEAD:
                await self._notify_state_change()

    def _cancel_all_pending_futures(self) -> None:
        """Cancel every in-flight pending Future.

        Used by provider ``interrupt_or_kill`` paths to unwind awaiters cleanly.
        The awaiter's ``finally`` clause does the dict cleanup; we just signal
        the cancellation here. Safe to call multiple times.
        """
        for future in self._pending_futures.values():
            if not future.done():
                future.cancel()

    def resolve_pending_request(self, request_id: str, response: Any) -> bool:
        """Resolve a specific pending request with the user's response.

        Called by the manager when a WebSocket response arrives from the
        frontend. ``request_id`` disambiguates between concurrent pending
        requests on the same session (e.g. Claude's parallel Read + Glob).

        Returns ``True`` if the request was resolved, ``False`` if there was no
        matching in-flight Future (typically meaning the request was already
        resolved or the agent died in the meantime).
        """
        future = self._pending_futures.get(request_id)
        if future is None or future.done():
            with provider_log_context(self.provider):
                logger.warning(
                    "[session %s] resolve_pending_request: no in-flight Future "
                    "for request_id=%s (known=%s)",
                    self.session_id,
                    request_id,
                    list(self._pending_requests.keys()),
                )
            return False
        future.set_result(response)
        return True

    # ------------------------------------------------------------------
    # Process introspection
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        """Return the underlying subprocess PID if applicable.

        Subclasses backed by a subprocess override this. The default returns
        ``None`` so providers that don't run a subprocess (in-process SDKs)
        can leave it untouched.
        """
        return None

    def get_memory_rss(self) -> int | None:
        """Return RSS memory of the underlying subprocess if known."""
        try:
            pid = self.get_pid()
            if pid is None:
                return None
            return get_process_memory(pid)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_info(self) -> AgentInfo:
        """Build an immutable snapshot of the current agent state.

        Subclasses can override this to populate ``active_tools`` and
        ``last_started_tool_id`` by calling ``super()`` and ``_replace``-ing
        the result. ``pending_requests`` is always populated here.
        """
        # The subprocess no longer exists past DEAD — skip the memory lookup.
        memory_rss = None if self.state == AgentState.DEAD else self.get_memory_rss()
        return AgentInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            provider=self.provider,
            state=self.state,
            previous_state=self.previous_state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
            pending_requests=self.pending_requests,
        )

    # ------------------------------------------------------------------
    # Lifecycle (abstract)
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        **kwargs: Any,
    ) -> None:
        """Start the agent and process the first message.

        Implementations must:

        - Register ``on_state_change`` so ``_notify_state_change`` dispatches to it.
        - Drive the state machine through STARTING → ASSISTANT_TURN → USER_TURN
          (or → DEAD on failure).
        - Never raise: errors are reported via the DEAD state and ``error``.
        """
        raise NotImplementedError

    async def send(self, text: str, **kwargs: Any) -> None:
        """Send a follow-up message to the running agent."""
        raise NotImplementedError

    async def interrupt_or_kill(self, reason: str) -> None:
        """Interrupt the agent gracefully, falling back to a hard kill."""
        raise NotImplementedError
