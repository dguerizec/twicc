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
        """Start every provider's orchestrator in parallel.

        Each :meth:`BaseOrchestrator.start` is non-blocking (it schedules
        tasks and returns), so ``gather`` mostly serves to overlap
        eventual init work. ``return_exceptions=True`` keeps a single
        provider's startup failure from cancelling the others — failures
        are logged so they don't go silent.

        ``search_index_ready`` is forwarded to every orchestrator so the
        ones that own a watcher (which writes to the global Tantivy
        index) can ``await`` it before starting that watcher.
        """
        results = await asyncio.gather(
            *(orch.start(shutdown_event, search_index_ready) for orch in self._orchestrators.values()),
            return_exceptions=True,
        )
        for (provider, _), result in zip(self.items(), results):
            if isinstance(result, BaseException):
                logger.error(
                    "Orchestrator for %s failed to start: %s",
                    provider.value, result, exc_info=result,
                )

    async def wait_initial_sync_done(self) -> None:
        """Block until every provider's initial sync has reported completion.

        Providers without an initial sync inherit a pre-set event from
        :class:`BaseOrchestrator` so this returns instantly for them.
        """
        await asyncio.gather(*(orch.initial_sync_done.wait() for orch in self._orchestrators.values()))

    async def wait_compute_done(self) -> None:
        """Block until every provider's background compute has reported completion.

        Same pre-set default as :meth:`wait_initial_sync_done` for
        providers without a compute phase.
        """
        await asyncio.gather(*(orch.compute_done.wait() for orch in self._orchestrators.values()))

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
        """Stop every provider's tasks in parallel.

        Each orchestrator owns its own task graph and teardown order, so
        parallel shutdown is safe — and faster when a provider has slow
        teardown work (waiting on a JSONL watcher to drain, killing SDK
        processes, ...). ``return_exceptions=True`` ensures a single
        failing provider doesn't leave the others' tasks dangling.
        """
        results = await asyncio.gather(
            *(orch.shutdown() for orch in self._orchestrators.values()),
            return_exceptions=True,
        )
        for (provider, _), result in zip(self.items(), results):
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
