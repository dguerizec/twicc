"""
Per-provider orchestrators and their cross-provider registry.

Each provider owns the lifecycle of its own async/background tasks
(JSONL watcher, sync loops, agent manager, ...) inside a subclass of
:class:`BaseOrchestrator`. The CLI server entry point doesn't know which
tasks any given provider runs — it just calls
``start_all`` / ``request_thread_stop_all`` / ``shutdown_all`` on the
:class:`OrchestratorRegistry`, mirroring the registry pattern used for
helpers (``ProviderHelpersRegistry``), agent managers
(``AgentManagerRegistry``) and WS handlers
(``WSConsumer.PROVIDER_HANDLERS``).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Coroutine
from typing import Any, ClassVar, TypeVar

from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.providers.state import (
    ProviderState,
    force_disable_after_failed_start,
    get_enabled_providers,
    set_provider_state,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseOrchestrator:
    """Lifecycle owner for one provider's async/background tasks.

    Subclasses must:

    - Set the :attr:`provider` ClassVar to the matching :class:`Provider`
      enum value.
    - Implement :meth:`start` (launch tasks; should return promptly once
      tasks are scheduled — ``run_server`` only awaits the call long
      enough to register them).
    - Implement :meth:`shutdown` (cancel tasks in the right order; should
      be best-effort and time-bounded).

    :meth:`request_thread_stop` has a no-op default — only providers that
    run blocking sync threads (e.g. Claude Code's initial JSONL sync)
    need to override it to signal those threads cooperatively.
    """

    provider: ClassVar[Provider]

    def __init__(self) -> None:
        # Fail fast if the subclass forgot to set its provider key. Without
        # this guard, a missing attribute would only surface deep inside
        # the registry's first iteration as an opaque ``AttributeError``.
        # Mirrors the same guard in ``BaseAgent``.
        if not getattr(type(self), "provider", None):
            raise TypeError(
                f"{type(self).__name__}.provider must be set to a "
                "Provider enum value."
            )

        # Cross-provider lifecycle signals owned by the CLI.
        #
        # ``initial_sync_done`` and ``compute_done`` are pre-set by default
        # so providers without those phases (e.g. Codex today) don't make
        # the CLI wait forever on ``await asyncio.gather(*(o.<event>.wait()
        # for o in orchestrators))``. Providers that do run the phase reset
        # the event in their ``__init__`` and call ``.set()`` once their
        # corresponding ``broadcast_startup_progress(..., completed=True)``
        # has fired.
        self.initial_sync_done = asyncio.Event()
        self.initial_sync_done.set()
        self.compute_done = asyncio.Event()
        self.compute_done.set()

        # Set by the CLI after ``init_search_index()`` returns. Providers
        # that run a JSONL watcher writing to the index must ``await`` this
        # before their watcher starts; providers without a watcher ignore it.
        self.search_index_ready: asyncio.Event | None = None

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch this provider's background tasks.

        ``shutdown_event`` is the shared CLI-level event set by the
        signal handler when the server begins to stop; long-running
        async loops that this orchestrator owns should watch it and
        bail out promptly.

        ``search_index_ready`` is a CLI-owned event set after the global
        ``init_search_index()`` returns. Providers whose watcher writes
        into the search index must ``await`` it before starting that
        watcher.
        """
        raise NotImplementedError

    def request_thread_stop(self) -> None:
        """Cooperative stop signal for blocking sync threads owned by this provider.

        Called from the CLI signal handler so blocking threads
        (e.g. an initial filesystem sync that walks disk inside
        ``asyncio.to_thread``) can exit promptly without waiting for
        their next chunk to finish.

        Async tasks listen for the shared ``shutdown_event`` passed to
        :meth:`start` instead — this hook is specifically for threads.

        Default: no-op. Providers without blocking sync threads don't
        need to override.
        """
        return None

    async def shutdown(self) -> None:
        """Stop this provider's tasks in dependency-safe order.

        Best-effort and time-bounded: an exception here must not prevent
        other providers' shutdowns from running (the registry catches
        them via ``return_exceptions=True``).
        """
        raise NotImplementedError

    def _create_task(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        """Schedule ``coro`` as a task tagged with this orchestrator's provider.

        Wrapper around :func:`asyncio.create_task` that runs the new task
        in a context where :data:`twicc.logging_context.current_provider`
        is set to ``self.provider.value``. Any log record emitted from
        the task — directly or through a ``sync_to_async`` /
        ``asyncio.to_thread`` call it makes, both of which propagate the
        context — is stamped with the right provider tag automatically.

        The contextvar is set inside a fresh :func:`contextvars.copy_context`
        so the orchestrator's own caller (e.g. the CLI startup task) is
        not retagged when this method returns. Sub-tasks created from
        within ``coro`` (including :func:`asyncio.create_task` calls)
        inherit the tagged context, so providers only need to route every
        long-running task through this helper at the orchestrator level.
        """
        ctx = contextvars.copy_context()
        ctx.run(current_provider.set, self.provider.value)
        return asyncio.create_task(coro, name=name, context=ctx)


class OrchestratorRegistry:
    """Singleton holding one :class:`BaseOrchestrator` per provider.

    Instances are created eagerly when the registry is built, so a
    second call to ``start_all`` after a ``shutdown_all`` would reuse
    the same instances — providers must reset their internal state in
    ``shutdown`` if they want to support that.

    The current CLI lifecycle (start once, run server, shutdown once)
    doesn't exercise the reuse path, but keeping the contract explicit
    avoids surprises if a future host (e.g. a hot-reload harness)
    decides to restart.
    """

    PROVIDER_ORCHESTRATORS: ClassVar[dict[Provider, type[BaseOrchestrator]]]

    def __init__(self) -> None:
        # Imported here to avoid a circular import at module load time:
        # each provider orchestrator imports models / registries that
        # eventually import from this package.
        from twicc.providers.claude_code.orchestrator import ClaudeCodeOrchestrator
        from twicc.providers.codex.orchestrator import CodexOrchestrator

        self.PROVIDER_ORCHESTRATORS = {
            Provider.CLAUDE_CODE: ClaudeCodeOrchestrator,
            Provider.CODEX: CodexOrchestrator,
        }
        self._orchestrators: dict[Provider, BaseOrchestrator] = {
            key: cls() for key, cls in self.PROVIDER_ORCHESTRATORS.items()
        }

        # Stashed by start_all() so start_one() / shutdown_one() can reuse them
        # on hot-toggle without the caller having to thread them through.
        self._shutdown_event: asyncio.Event | None = None
        self._search_index_ready: asyncio.Event | None = None

    def get(self, provider: Provider) -> BaseOrchestrator:
        """Return the orchestrator for ``provider``."""
        return self._orchestrators[provider]

    def items(self) -> list[tuple[Provider, BaseOrchestrator]]:
        """Return ``(provider, orchestrator)`` pairs for every registered provider."""
        return list(self._orchestrators.items())

    def values(self) -> list[BaseOrchestrator]:
        """Return the orchestrator instances for every registered provider."""
        return list(self._orchestrators.values())

    # ------------------------------------------------------------------
    # Aggregate operations driven by the CLI lifecycle
    # ------------------------------------------------------------------

    async def start_all(
        self,
        shutdown_event: asyncio.Event,
        search_index_ready: asyncio.Event,
    ) -> None:
        """Start every enabled provider's orchestrator in parallel.

        Each :meth:`BaseOrchestrator.start` is non-blocking (it schedules
        tasks and returns), so ``gather`` mostly serves to overlap
        eventual init work. ``return_exceptions=True`` keeps a single
        provider's startup failure from cancelling the others — failures
        are logged so they don't go silent.

        ``search_index_ready`` is forwarded to every orchestrator so the
        ones that own a watcher (which writes to the global Tantivy
        index) can ``await`` it before starting that watcher.

        Only providers listed by :func:`twicc.providers.state.get_enabled_providers`
        are started. If no provider is enabled (no choice made yet, or everything
        disabled), nothing is started and the app keeps serving the initial
        provider-selection dialog.

        The two events are stashed on the registry so :meth:`start_one` /
        :meth:`shutdown_one` can reuse them later for hot-toggle.
        """
        # Stash so start_one() / shutdown_one() can reuse on hot-toggle.
        self._shutdown_event = shutdown_event
        self._search_index_ready = search_index_ready

        enabled = get_enabled_providers()
        if not enabled:
            return

        enabled_items = [(provider, orch) for provider, orch in self._orchestrators.items() if provider in enabled]
        tasks = [
            asyncio.create_task(
                self._start_with_state(provider, orch),
                context=self._provider_context(provider),
            )
            for provider, orch in enabled_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (provider, _), result in zip(enabled_items, results):
            if isinstance(result, BaseException):
                logger.error(
                    "Orchestrator for %s failed to start: %s",
                    provider.value, result, exc_info=result,
                )

    async def wait_initial_sync_done(self) -> None:
        """Block until every enabled provider's initial sync has reported completion.

        Providers without an initial sync inherit a pre-set event from
        :class:`BaseOrchestrator` so this returns instantly for them.

        Only waits on providers that were actually started (i.e. enabled ones).
        Waiting on an un-started provider's event would hang forever because
        ``start()`` is what triggers ``.set()`` on it.
        """
        enabled = get_enabled_providers()
        if not enabled:
            return
        await asyncio.gather(*(
            orch.initial_sync_done.wait()
            for provider, orch in self._orchestrators.items()
            if provider in enabled
        ))

    async def wait_compute_done(self) -> None:
        """Block until every enabled provider's background compute has reported completion.

        Same pre-set default as :meth:`wait_initial_sync_done` for
        providers without a compute phase. Only waits on enabled providers
        (same reasoning as :meth:`wait_initial_sync_done`).
        """
        enabled = get_enabled_providers()
        if not enabled:
            return
        await asyncio.gather(*(
            orch.compute_done.wait()
            for provider, orch in self._orchestrators.items()
            if provider in enabled
        ))

    async def _start_with_state(self, provider: Provider, orch: BaseOrchestrator) -> None:
        """Wrap ``orch.start()`` with state transitions and failure handling.

        Flow:

        - ``stopped → starting`` (broadcast)
        - ``await orch.start(...)``
        - On success: ``starting → running`` (broadcast)
        - On failure: roll back to ``stopped``, persist the provider into
          ``disabledProviders`` so the UI switch flips off, and re-raise
          so the caller's error handling (e.g. ``gather`` with
          ``return_exceptions=True``) sees the exception.

        ``self._shutdown_event`` and ``self._search_index_ready`` must have
        been set by ``start_all`` already (both start paths go through it).
        """
        assert self._shutdown_event is not None
        assert self._search_index_ready is not None
        await set_provider_state(provider, ProviderState.STARTING)
        try:
            await orch.start(self._shutdown_event, self._search_index_ready)
        except BaseException:
            logger.exception("Failed to start orchestrator for %s — forcing disable", provider.value)
            await set_provider_state(provider, ProviderState.STOPPED)
            await force_disable_after_failed_start(provider)
            raise
        await set_provider_state(provider, ProviderState.RUNNING)

    async def _shutdown_with_state(self, provider: Provider, orch: BaseOrchestrator) -> None:
        """Wrap ``orch.shutdown()`` with state transitions.

        ``running → stopping`` (broadcast), run the shutdown, then end in
        ``stopped`` unconditionally — even if shutdown raised. The task
        graph is gone either way; staying in ``stopping`` would leave the
        UI permanently busy.
        """
        await set_provider_state(provider, ProviderState.STOPPING)
        try:
            await orch.shutdown()
        finally:
            await set_provider_state(provider, ProviderState.STOPPED)

    @staticmethod
    def _provider_context(provider: Provider) -> contextvars.Context:
        """Return a fresh Context with ``current_provider`` tagged for ``provider``.

        Mirrors :meth:`BaseOrchestrator._create_task` but for one-shot
        awaitables. ``start()`` and ``shutdown()`` themselves log synchronous
        lines (and ``start()`` even awaits sync work like
        ``ensure_twicc_plugin_installed``) before spawning the long-running
        tasks. Those synchronous log lines must carry the provider tag too,
        otherwise they fall back to ``"global"`` because the caller has no
        provider context (the WS handler for hot-toggle, the CLI startup
        task for ``start_all``).

        Returning a fresh ``Context`` lets us schedule the call via
        ``asyncio.create_task(..., context=...)``, which isolates the
        tag to the new task without touching the caller's context — safe
        with ``gather`` because each scheduled task gets its own context.
        """
        ctx = contextvars.copy_context()
        ctx.run(current_provider.set, provider.value)
        return ctx

    async def start_one(self, provider: Provider) -> None:
        """Start a single provider's orchestrator (used by hot-toggle on activation).

        Requires :meth:`start_all` to have been called first so the shutdown
        and search-index events are available. Raises :exc:`RuntimeError`
        immediately if called before :meth:`start_all` — silent misbehaviour
        would be worse than a loud failure.
        """
        if self._shutdown_event is None or self._search_index_ready is None:
            raise RuntimeError("OrchestratorRegistry.start_one called before start_all")
        orch = self._orchestrators.get(provider)
        if orch is None:
            return
        await asyncio.create_task(
            self._start_with_state(provider, orch),
            context=self._provider_context(provider),
        )

    async def shutdown_one(self, provider: Provider) -> None:
        """Shutdown a single provider's orchestrator (used by hot-toggle on deactivation)."""
        orch = self._orchestrators.get(provider)
        if orch is None:
            return
        await asyncio.create_task(
            self._shutdown_with_state(provider, orch),
            context=self._provider_context(provider),
        )

    def request_thread_stop_all(self) -> None:
        """Signal every provider's blocking sync threads to stop.

        Synchronous (called from the signal handler). Providers without
        blocking threads inherit the no-op default.
        """
        for orch in self._orchestrators.values():
            try:
                orch.request_thread_stop()
            except Exception as e:  # noqa: BLE001 — must not block other providers
                logger.error(
                    "Orchestrator %s.request_thread_stop failed: %s",
                    type(orch).__name__, e, exc_info=True,
                )

    async def shutdown_all(self) -> None:
        """Stop every enabled provider's tasks in parallel.

        Filters on the **currently enabled** providers (i.e. the present
        value of :func:`get_enabled_providers`). Under normal operation this
        is the right set: a provider's ``start()`` was either invoked by
        ``start_all`` at boot, or by ``start_one`` on a later hot-toggle.
        Calling ``shutdown()`` on an orchestrator whose ``start()`` was
        never invoked can raise ``AttributeError`` on attributes that are
        only created during startup (e.g. a watcher task).

        Edge case worth noting: if ``start_all`` raised for a specific
        provider (caught by ``return_exceptions=True``), that provider may
        still be in the "enabled" set without having been successfully
        started. In that situation, ``shutdown()`` may raise. The error is
        logged and does not affect the other providers' teardown.

        Each orchestrator owns its own task graph and teardown order, so
        parallel shutdown is safe — and faster when a provider has slow
        teardown work (waiting on a JSONL watcher to drain, killing SDK
        processes, ...). ``return_exceptions=True`` ensures a single
        failing provider doesn't leave the others' tasks dangling.
        """
        enabled = get_enabled_providers()
        if not enabled:
            return
        enabled_items = [(p, o) for p, o in self._orchestrators.items() if p in enabled]
        tasks = [
            asyncio.create_task(
                self._shutdown_with_state(provider, orch),
                context=self._provider_context(provider),
            )
            for provider, orch in enabled_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (provider, _), result in zip(enabled_items, results):
            if isinstance(result, BaseException):
                logger.error(
                    "Orchestrator for %s failed to shut down cleanly: %s",
                    provider.value, result, exc_info=result,
                )


_registry: OrchestratorRegistry | None = None


def get_orchestrator_registry() -> OrchestratorRegistry:
    """Return the global :class:`OrchestratorRegistry` (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = OrchestratorRegistry()
    return _registry
