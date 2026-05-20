"""
Background compute producer.

Processes existing sessions that need metadata computation at startup, then
stops. New sessions created by the watcher get compute_version set at
creation time.

Architecture:
- A separate Process per provider handles CPU-intensive work (JSON parsing,
  metadata computation). The worker process only READS from the database
  (WAL mode supports multiple readers).
- The worker pushes its results onto the process-wide shared compute result
  queue owned by :mod:`twicc.providers.db_writer`; the unified DB writer
  applies every write. This module is purely the *producer* side — it never
  writes to the database from the main process.

This module is provider-agnostic: each provider's orchestrator builds a
:class:`ComputeContext` carrying its ``Provider`` enum value, its
``compute_version`` setting, and a ``compute_factory`` dotted path pointing
at its own :class:`~twicc.providers.compute_base.BaseSessionCompute`
factory. The factory is stored as a string so it survives the spawn-worker
pickle without dragging the provider's compute module into the child
process before ``django.setup()`` has run.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import multiprocessing
import queue
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import orjson
from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.startup_progress import broadcast_startup_progress

if TYPE_CHECKING:
    from twicc.providers.compute_base import BaseSessionCompute

# NOTE: Django model imports (twicc.core.models, twicc.core.serializers, and
# any provider compute module that imports them) are intentionally NOT imported
# at module level. The "spawn" start method re-imports this module in the child
# process before django.setup() runs. Top-level model imports would trigger
# AppRegistryNotReady. All such imports are done inside functions instead.
# For the same reason, db_writer (which the main process needs here) is only
# imported lazily inside functions, never at module level.

logger = logging.getLogger(__name__)

# Use "spawn" start method to avoid fork-safety issues.
# The default "fork" method on Linux can deadlock when the parent process has
# multiple threads (event loop, sync_to_async thread pool, etc.) because the
# child inherits all locks in their current state but the threads that held them
# no longer exist. "spawn" starts a fresh Python interpreter, avoiding this entirely.
_mp_ctx = multiprocessing.get_context("spawn")


@dataclass
class ComputeContext:
    """Mutable state for the background compute pipeline.

    Created once at startup by a provider's orchestrator and passed
    explicitly to all functions that need access to the compute
    infrastructure. The provider injects:

    - ``provider``: the :class:`~twicc.core.enums.Provider` enum value used
      both for ORM filters and (via ``.value``) as the wire key on
      startup-progress broadcasts so the frontend can aggregate per-phase
      totals across providers.
    - ``compute_version``: the current target compute version for this
      provider (sessions whose stored ``compute_version`` differs are the
      ones that need recomputation).
    - ``compute_factory``: a ``"module:attribute"`` dotted path to the
      provider's :class:`~twicc.providers.compute_base.BaseSessionCompute`
      factory (e.g. ``"twicc.providers.claude_code.compute:get_compute"``).
      Stored as a string — not a callable — so the spawn worker can carry
      this value through pickle without importing the provider's compute
      module before ``django.setup()`` has run in the child process. The
      module is imported lazily on first :meth:`get_compute` call.

    The worker's result queue is NOT held here — it is the process-wide
    shared queue owned by :mod:`twicc.providers.db_writer`. Only the
    ``command_queue`` (one-way main→worker) is per-context.
    """

    provider: Provider
    compute_version: int
    compute_factory: str
    command_queue: _mp_ctx.Queue = field(default_factory=_mp_ctx.Queue)
    worker_stop_event: _mp_ctx.Event = field(default_factory=_mp_ctx.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: _mp_ctx.Process | None = None
    # Set by start_background_compute_task before the worker is spawned. The
    # worker tags every result message with it so the unified DB writer can
    # isolate this run from stale messages of a cancelled one.
    run_id: int = 0

    def get_compute(self) -> BaseSessionCompute:
        """Resolve :attr:`compute_factory` and return a fresh compute instance.

        Imports the provider's compute module on demand. Caller must have
        already run ``django.setup()`` in its process, since the compute
        module imports Django models at module level.
        """
        return _resolve_factory(self.compute_factory)()


def _resolve_factory(dotted_path: str):
    """Resolve a ``"module:attribute"`` path into the referenced object."""
    module_name, attr = dotted_path.split(":")
    return getattr(importlib.import_module(module_name), attr)


async def stop_background_task(ctx: ComputeContext) -> None:
    """Signal the background compute task to stop and terminate worker process.

    Async so the blocking ``process.join()`` calls can run off the event loop:
    joining on the loop thread would freeze the unified DB writer's consumer,
    and a worker blocked on a full result queue only makes progress (drains,
    then exits) while that consumer keeps running.
    """
    logger.info("stop_background_task: starting shutdown...")

    # Signal asyncio tasks to stop
    ctx.stop_event.set()
    logger.info("stop_background_task: asyncio stop_event set")

    # Signal worker process to stop via multiprocessing event
    ctx.worker_stop_event.set()
    logger.info("stop_background_task: worker_stop_event set")

    # Send stop signal to worker process via queue (backup)
    try:
        ctx.command_queue.put_nowait(None)  # None = stop signal
        logger.info("stop_background_task: stop signal sent to queue")
    except Exception as e:
        logger.warning(f"stop_background_task: failed to send stop signal to queue: {e}")

    # Wait for worker process to exit gracefully, then terminate if needed.
    # join() runs off the event loop (asyncio.to_thread) so the consumer
    # keeps draining while we wait — terminate()/kill() are non-blocking
    # signals and stay on the loop.
    if ctx.process is not None and ctx.process.is_alive():
        logger.info(f"stop_background_task: waiting for worker process (PID: {ctx.process.pid}) to exit...")
        await asyncio.to_thread(ctx.process.join, 2.0)
        if ctx.process.is_alive():
            logger.warning("stop_background_task: worker process still alive, terminating...")
            ctx.process.terminate()
            await asyncio.to_thread(ctx.process.join, 1.0)
            if ctx.process.is_alive():
                logger.error("stop_background_task: worker process did not terminate, killing...")
                ctx.process.kill()
                await asyncio.to_thread(ctx.process.join, 0.5)
        else:
            logger.info("stop_background_task: worker process exited gracefully")
        ctx.process = None
    else:
        logger.info("stop_background_task: no worker process to stop")

    # Cancel the command queue's feeder thread to prevent blocking on shutdown.
    # The result queue is the process-wide shared queue owned by db_writer —
    # it is NOT closed here; it outlives every per-provider compute run.
    with suppress(Exception):
        ctx.command_queue.cancel_join_thread()
        ctx.command_queue.close()

    logger.info("stop_background_task: shutdown complete")


# =============================================================================
# Worker Process Functions (run in separate process)
# =============================================================================


def _set_pdeathsig_linux() -> None:
    """Ask the kernel to send SIGTERM to this process when its parent dies (Linux only).

    Calls ``prctl(PR_SET_PDEATHSIG, SIGTERM)`` so a hard kill of the main
    TwiCC server doesn't leave this worker orphaned still holding the
    Tantivy writer lock — which is what blocked the next ``twicc`` start
    until a manual ``pkill``. Must be called inside the child process,
    after spawn/fork, so the setting applies to the child rather than
    the parent.

    No-op on macOS and Windows: there is no portable equivalent there,
    so a hard kill of the parent on those platforms still leaves the
    worker orphaned. That is a known limitation, not a regression
    introduced by this code.

    Best-effort: any failure is logged at DEBUG level and the worker
    keeps running with the same lifetime semantics it had before.
    """
    import sys
    if sys.platform != "linux":
        return
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        import signal as _signal
        if libc.prctl(PR_SET_PDEATHSIG, _signal.SIGTERM, 0, 0, 0) != 0:
            logger.debug(
                "prctl(PR_SET_PDEATHSIG) failed: errno=%d",
                ctypes.get_errno(),
            )
    except Exception as exc:
        logger.debug("Could not set PR_SET_PDEATHSIG: %s", exc)


def compute_worker_main(command_queue, result_queue, stop_event, compute_factory: str, run_id: int) -> None:
    """
    Main function running in the compute worker process.

    Receives session_ids via command_queue, computes metadata (READ-ONLY DB access),
    sends update batches via result_queue (the process-wide shared queue).

    This function runs in a separate process and must initialize Django itself.
    ``compute_factory`` is the dotted ``"module:attribute"`` path of the
    provider's compute factory; it is resolved AFTER ``django.setup()`` so
    importing the provider's compute module (which pulls in Django models)
    does not crash the spawn worker before the app registry is ready.

    Every message put on ``result_queue`` carries a ``'provider'`` key (for
    routing/logging) and this run's ``run_id``, so the unified consumer can
    isolate this run from a cancelled run's stale messages.
    """
    # Tie the worker's lifetime to the parent's on Linux: if the main TwiCC
    # process dies hard (SIGKILL, panic), the kernel sends us SIGTERM, which
    # default-handles to a clean exit. Without this, the orphaned worker would
    # keep holding the Tantivy writer lock and block the next ``twicc`` start.
    # Done before any other setup so the window where we could be orphaned is
    # as small as possible.
    _set_pdeathsig_linux()

    import signal

    # Ensure default signal handling — with "fork" mode the child would inherit
    # the parent's custom SIGTERM/SIGINT handlers that don't actually exit.
    # With "spawn" this is already the case, but we set it explicitly as a safeguard.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    import django
    django.setup()

    import logging
    worker_logger = logging.getLogger(__name__)

    compute = _resolve_factory(compute_factory)()
    provider_value = compute.provider.value
    # Tag every subsequent log line emitted by this worker process with
    # the provider this worker was spawned for. The worker process has a
    # fresh ContextVar default, so this set() is what makes log lines
    # show up under the right provider rather than ``"global"``.
    current_provider.set(provider_value)
    worker_logger.info("Compute worker process started")

    while True:
        try:
            # Check stop signal before getting command
            if stop_event.is_set():
                worker_logger.info("Compute worker received stop signal (event)")
                break

            # Blocking get with timeout (allows checking for stop signal)
            try:
                command = command_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Check stop event again after getting command
            if stop_event.is_set():
                worker_logger.info("Compute worker received stop signal (event)")
                break

            # None = stop signal (backup method)
            if command is None:
                worker_logger.info("Compute worker received stop signal (queue)")
                break

            session_id = command.get('session_id')
            if session_id:
                try:
                    # This function reads DB and sends batches via result_queue
                    compute.compute_session_metadata(session_id, result_queue, run_id)
                except Exception as e:
                    worker_logger.error(f"Error computing session {session_id}: {e}", exc_info=True)
                    # Flush the file handler so the error survives process termination
                    with suppress(Exception):
                        for handler in logging.getLogger('twicc').handlers:
                            handler.flush()
                    result_queue.put(orjson.dumps({
                        'type': 'error',
                        'provider': provider_value,
                        'run_id': run_id,
                        'session_id': session_id,
                        'error': str(e),
                    }))

        except Exception as e:
            worker_logger.error(f"Unexpected error in compute worker: {e}", exc_info=True)
            with suppress(Exception):
                for handler in logging.getLogger('twicc').handlers:
                    handler.flush()

    # Signal the consumer that the worker is done
    result_queue.put(orjson.dumps({'type': 'done', 'provider': provider_value, 'run_id': run_id}))
    worker_logger.info("Compute worker sent 'done' signal")

    # Drain command queue before exiting to prevent blocking
    worker_logger.info("Compute worker draining command queue...")
    drained = 0
    while True:
        try:
            command_queue.get_nowait()
            drained += 1
        except queue.Empty:
            break
    if drained:
        worker_logger.info(f"Compute worker drained {drained} commands from queue")

    worker_logger.info("Compute worker process stopped")


def start_compute_process(ctx: ComputeContext) -> None:
    """Start the compute worker process if not already running.

    The worker writes onto the process-wide shared compute result queue
    owned by :mod:`twicc.providers.db_writer` (lazy-imported here so this
    module is never dragged into the spawn subprocess before Django setup).
    """
    if ctx.process is None or not ctx.process.is_alive():
        from twicc.providers.db_writer import get_compute_result_queue

        result_queue = get_compute_result_queue()
        # Reset stop event for new process
        ctx.worker_stop_event = _mp_ctx.Event()
        ctx.process = _mp_ctx.Process(
            target=compute_worker_main,
            args=(ctx.command_queue, result_queue, ctx.worker_stop_event, ctx.compute_factory, ctx.run_id),
            daemon=True,
            name="compute-worker",
        )
        ctx.process.start()
        logger.info(f"Started compute worker process (PID: {ctx.process.pid})")


async def start_background_compute_task(ctx: ComputeContext) -> None:
    """
    Background task that processes existing sessions needing computation at startup.

    Architecture:
    - Starts a separate Process for CPU-intensive work (JSON parsing, metadata computation)
    - The worker process only READS from the database
    - All database WRITES happen via the unified DB writer (:mod:`db_writer`),
      which serializes writes across providers and phases

    This task processes all sessions with outdated or missing compute_version,
    then stops. New sessions created by the watcher get compute_version set
    at creation time, so they don't need background reprocessing.
    """
    from twicc.providers.db_writer import arm_compute_completion
    from twicc.projects import load_project_directories, load_project_git_roots
    from twicc.core.models import Session, SessionType

    provider_value = ctx.provider.value

    # Count sessions needing computation
    total_to_compute = await sync_to_async(Session.objects.filter(
        provider=ctx.provider,
    ).exclude(
        compute_version=ctx.compute_version
    ).count)()

    if total_to_compute == 0:
        logger.info("Background compute: no sessions to process")
        # Report the total session count so the frontend can show "N/N" instead of "0/0"
        total_display = await sync_to_async(
            Session.objects.filter(
                provider=ctx.provider, type=SessionType.SESSION,
            ).count
        )()
        await broadcast_startup_progress(
            "background_compute", total_display, total_display,
            provider=provider_value, completed=True,
        )
        return

    # Count only real sessions (not subagents) for progress display.
    # The actual compute processes ALL sessions, but users only care about session count.
    sessions_to_display = await sync_to_async(
        lambda: set(
            Session.objects.filter(
                provider=ctx.provider, type=SessionType.SESSION,
            )
            .exclude(compute_version=ctx.compute_version)
            .values_list("id", flat=True)
        )
    )()
    total_display = len(sessions_to_display)

    # Broadcast initial progress state (0/N) — using display total (sessions only)
    await broadcast_startup_progress(
        "background_compute", 0, total_display, provider=provider_value
    )

    # Load project caches at startup
    await sync_to_async(load_project_directories)()
    await sync_to_async(load_project_git_roots)()

    # Arm the completion before spawning the worker: arm_compute_completion()
    # mints the run_id, and the worker must be spawned with it so every result
    # message it emits is tagged for this run. The Event is set when the
    # writer drains this run's 'done' message — i.e. once every
    # session_complete this run produced has been applied.
    run_id, done_event = arm_compute_completion(ctx.provider, sessions_to_display, total_display)
    ctx.run_id = run_id

    # Start the worker process
    start_compute_process(ctx)

    logger.info(f"Background compute task started ({total_to_compute} sessions to process)")

    try:
        # Load all session IDs needing computation in one query, ordered by most recent first
        session_ids_to_compute = await sync_to_async(
            lambda: list(
                Session.objects
                .filter(provider=ctx.provider)
                .exclude(compute_version=ctx.compute_version)
                .order_by('-mtime')
                .values_list('id', flat=True)
            )
        )()

        # Send all session IDs to the worker process at once
        for session_id in session_ids_to_compute:
            if ctx.stop_event.is_set():
                break
            ctx.command_queue.put({'session_id': session_id})

        logger.info(f"Background compute: all {len(session_ids_to_compute)} sessions sent to worker")

        # Send stop signal to worker so it finishes and sends 'done'
        ctx.command_queue.put(None)

        # Wait for the unified DB writer to drain everything for this provider
        # (the worker has emitted 'done' and any pending flushes have run).
        await done_event.wait()

        # Broadcast completion (using display total — sessions only, not subagents)
        await broadcast_startup_progress(
            "background_compute", total_display, total_display,
            provider=provider_value, completed=True,
        )
    finally:
        # Stop the worker process. Idempotent if it has already exited.
        await stop_background_task(ctx)

    logger.info("Background compute task completed")
