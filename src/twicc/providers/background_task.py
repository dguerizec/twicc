"""
Background compute task for session metadata.

Processes existing sessions that need metadata computation at startup, then stops.
New sessions created by the watcher get compute_version set at creation time.

Architecture:
- A separate Process per provider handles CPU-intensive work (JSON parsing,
  metadata computation). The worker process only READS from the database
  (WAL mode supports multiple readers).
- All database WRITES happen in a single, cross-provider consumer that drains
  every registered provider's result queue from the main process event loop.
  This serializes the DB writer side and eliminates "database is locked"
  errors that arise when multiple providers' consumers race for the SQLite
  write lock.

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
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import TYPE_CHECKING

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.startup_progress import broadcast_startup_progress
from twicc.workspaces import auto_add_project_to_workspaces

if TYPE_CHECKING:
    from twicc.providers.compute_base import BaseSessionCompute

# NOTE: Django model imports (twicc.core.models, twicc.core.serializers, and
# any provider compute module that imports them) are intentionally NOT imported
# at module level. The "spawn" start method re-imports this module in the child
# process before django.setup() runs. Top-level model imports would trigger
# AppRegistryNotReady. All such imports are done inside functions instead.

logger = logging.getLogger(__name__)

# How often to broadcast project_updated during background compute (every N sessions per project).
# Lower = more responsive cost updates in the UI, higher = less frontend overhead.
PROJECT_BROADCAST_INTERVAL = 5

# Batch size for activity recalculation flushes (per provider).
BATCH_ACTIVITY_COUNT = 50

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
    """

    provider: Provider
    compute_version: int
    compute_factory: str
    command_queue: _mp_ctx.Queue = field(default_factory=_mp_ctx.Queue)
    result_queue: _mp_ctx.Queue = field(default_factory=_mp_ctx.Queue)
    worker_stop_event: _mp_ctx.Event = field(default_factory=_mp_ctx.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: _mp_ctx.Process | None = None

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


def stop_background_task(ctx: ComputeContext) -> None:
    """Signal the background compute task to stop and terminate worker process."""
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

    # Wait for worker process to exit gracefully, then terminate if needed
    if ctx.process is not None and ctx.process.is_alive():
        logger.info(f"stop_background_task: waiting for worker process (PID: {ctx.process.pid}) to exit...")
        ctx.process.join(timeout=2.0)
        if ctx.process.is_alive():
            logger.warning("stop_background_task: worker process still alive, terminating...")
            ctx.process.terminate()
            ctx.process.join(timeout=1.0)
            if ctx.process.is_alive():
                logger.error("stop_background_task: worker process did not terminate, killing...")
                ctx.process.kill()
                ctx.process.join(timeout=0.5)
        else:
            logger.info("stop_background_task: worker process exited gracefully")
        ctx.process = None
    else:
        logger.info("stop_background_task: no worker process to stop")

    # Cancel queue threads to prevent blocking on shutdown
    # (don't wait for queue data to be flushed)
    with suppress(Exception):
        ctx.command_queue.cancel_join_thread()
        ctx.command_queue.close()
    with suppress(Exception):
        ctx.result_queue.cancel_join_thread()
        ctx.result_queue.close()

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


def compute_worker_main(command_queue, result_queue, stop_event, compute_factory: str) -> None:
    """
    Main function running in the compute worker process.

    Receives session_ids via command_queue, computes metadata (READ-ONLY DB access),
    sends update batches via result_queue.

    This function runs in a separate process and must initialize Django itself.
    ``compute_factory`` is the dotted ``"module:attribute"`` path of the
    provider's compute factory; it is resolved AFTER ``django.setup()`` so
    importing the provider's compute module (which pulls in Django models)
    does not crash the spawn worker before the app registry is ready.
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
    # Tag every subsequent log line emitted by this worker process with
    # the provider this worker was spawned for. The worker process has a
    # fresh ContextVar default, so this set() is what makes log lines
    # show up under the right provider rather than ``"global"``.
    current_provider.set(compute.provider.value)
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
                    compute.compute_session_metadata(session_id, result_queue)
                except Exception as e:
                    worker_logger.error(f"Error computing session {session_id}: {e}", exc_info=True)
                    # Flush the file handler so the error survives process termination
                    with suppress(Exception):
                        for handler in logging.getLogger('twicc').handlers:
                            handler.flush()
                    result_queue.put(orjson.dumps({
                        'type': 'error',
                        'session_id': session_id,
                        'error': str(e),
                    }))

        except Exception as e:
            worker_logger.error(f"Unexpected error in compute worker: {e}", exc_info=True)
            with suppress(Exception):
                for handler in logging.getLogger('twicc').handlers:
                    handler.flush()

    # Signal the consumer that the worker is done
    result_queue.put(orjson.dumps({'type': 'done'}))
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
    """Start the compute worker process if not already running."""
    if ctx.process is None or not ctx.process.is_alive():
        # Reset stop event for new process
        ctx.worker_stop_event = _mp_ctx.Event()
        ctx.process = _mp_ctx.Process(
            target=compute_worker_main,
            args=(ctx.command_queue, ctx.result_queue, ctx.worker_stop_event, ctx.compute_factory),
            daemon=True,
            name="compute-worker",
        )
        ctx.process.start()
        logger.info(f"Started compute worker process (PID: {ctx.process.pid})")


async def _handle_compute_done(session_id: str) -> None:
    """Handle completion of a session compute - broadcast session_updated.

    Only broadcasts for real sessions (not subagents) that have user messages.
    Subagents don't appear in session lists but each broadcast would trigger
    addSession in the frontend store, causing O(n²) getter re-evaluations.
    Subagent data is fetched on-demand via API when the user opens a subagent tab.

    project_updated broadcasts are handled separately by the unified consumer
    with throttling (every N sessions per project) to reduce frontend overhead.
    """
    from twicc.core.models import Session, SessionType
    from twicc.core.serializers import serialize_session

    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)

        # Only broadcast real sessions with user messages
        if session.user_message_count == 0 or session.type != SessionType.SESSION:
            return

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )
    except Session.DoesNotExist:
        logger.warning(f"Session {session_id} not found for broadcast")
    except Exception as e:
        logger.error(f"Error broadcasting updates for {session_id}: {e}")


async def _broadcast_project_updated(project_id: str) -> None:
    """Broadcast project_updated for a single project."""
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    try:
        if project := await sync_to_async(Project.objects.filter(id=project_id).first)():
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )
    except Exception as e:
        logger.error(f"Error broadcasting project_updated for {project_id}: {e}")


@sync_to_async
def _flush_pending_activities(provider: Provider, pending_activity_days: dict[str, set]) -> None:
    """Flush accumulated activity recalculations for all projects."""
    from twicc.core.models import PeriodicActivity
    for project_id, days in pending_activity_days.items():
        PeriodicActivity.recalculate_for_days(project_id, days, provider=provider, do_global=False)
    days = set.union(*pending_activity_days.values())
    PeriodicActivity.recalculate_for_days(None, days, provider=provider, do_global=True)


# =============================================================================
# Unified consumer (cross-provider DB writer)
# =============================================================================


@dataclass
class _ConsumerEntry:
    """One provider's slot inside the unified consumer.

    Holds the provider-specific :class:`ComputeContext` plus the per-run
    counters and pending-broadcast/activity buffers consumed by
    :func:`_process_compute_message`.
    """

    ctx: ComputeContext
    worker_done_event: asyncio.Event
    display_session_ids: set[str] | None
    total_display: int
    compute: BaseSessionCompute
    pending_activity_days: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    pending_project_ids: set[str] = field(default_factory=set)
    auto_added_project_ids: set[str] = field(default_factory=set)
    sessions_since_project_broadcast: int = 0
    sessions_since_activities_flush: int = 0
    completed_count: int = 0
    finalized: bool = False


_unified_entries: dict[Provider, _ConsumerEntry] = {}
_unified_consumer_task: asyncio.Task | None = None


def register_compute_consumer(
    ctx: ComputeContext,
    worker_done_event: asyncio.Event,
    *,
    display_session_ids: set[str] | None,
    total_display: int,
) -> None:
    """Register a provider's compute context with the unified consumer.

    Lazily starts :func:`_unified_consumer_loop` if no loop is currently
    running. ``worker_done_event`` is set by the loop when this provider's
    worker emits ``'done'`` (or when the loop sees ``ctx.stop_event`` set,
    or when the loop exits in its ``finally`` block).
    """
    entry = _ConsumerEntry(
        ctx=ctx,
        worker_done_event=worker_done_event,
        display_session_ids=display_session_ids,
        total_display=total_display,
        compute=ctx.get_compute(),
    )
    _unified_entries[ctx.provider] = entry
    _ensure_unified_consumer_running()
    logger.info(
        f"Unified consumer: registered provider={ctx.provider.value} "
        f"(active={[p.value for p in _unified_entries.keys()]})"
    )


def unregister_compute_consumer(provider: Provider) -> None:
    """Remove ``provider`` from the unified consumer's entry map.

    Idempotent. The consumer task exits naturally on its next tick once
    the map is empty; it will be relaunched lazily on the next
    :func:`register_compute_consumer` call.
    """
    if _unified_entries.pop(provider, None) is not None:
        logger.info(
            f"Unified consumer: unregistered provider={provider.value} "
            f"(active={[p.value for p in _unified_entries.keys()]})"
        )


def _ensure_unified_consumer_running() -> None:
    """Start :func:`_unified_consumer_loop` if no task is currently running."""
    global _unified_consumer_task
    if _unified_consumer_task is None or _unified_consumer_task.done():
        _unified_consumer_task = asyncio.create_task(_unified_consumer_loop())


async def _unified_consumer_loop() -> None:
    """Cross-provider drain loop. One coroutine, one DB writer.

    Polls every registered provider's :attr:`ComputeContext.result_queue`
    in a single asyncio task. All ``apply_session_complete`` calls — and
    the surrounding broadcasts, activity flushes, and workspace auto-adds —
    happen here sequentially, so no two providers ever race on the SQLite
    write lock.

    Exits when ``_unified_entries`` is empty. Restarted lazily by
    :func:`register_compute_consumer` whenever a new provider needs
    draining.
    """
    try:
        while _unified_entries:
            any_processed = False

            for provider, entry in list(_unified_entries.items()):
                if entry.finalized:
                    continue
                # ``stop_background_task`` sets the per-ctx stop_event when a
                # provider shuts down (or in shutdown() before the compute
                # task has emitted 'done'). Finalize the entry so the
                # awaiter unblocks.
                if entry.ctx.stop_event.is_set():
                    await _finalize_entry(entry)
                    continue

                try:
                    raw_msg = entry.ctx.result_queue.get_nowait()
                except queue.Empty:
                    continue
                except (OSError, ValueError):
                    # Queue may have been closed by stop_background_task
                    # racing with us. Treat as terminal for this entry.
                    await _finalize_entry(entry)
                    continue

                any_processed = True
                try:
                    msg = orjson.loads(raw_msg)
                except Exception:
                    logger.error(f"Failed to deserialize result message: {raw_msg!r:.500}")
                    continue

                await _process_compute_message(entry, msg)

            if not any_processed:
                await asyncio.sleep(0.05)
            else:
                # Yield to event loop between processed messages so other
                # coroutines (watchers, broadcasts, etc.) get scheduled.
                await asyncio.sleep(0)

    except Exception as exc:
        logger.error(f"Unified compute consumer crashed: {exc}", exc_info=True)
    finally:
        # Make sure no waiter is left hanging even if we crashed or were
        # cancelled mid-drain. Iterate on a snapshot since _finalize_entry
        # touches the dict via finalized flag (not the dict itself).
        for entry in list(_unified_entries.values()):
            if not entry.finalized:
                entry.finalized = True
                entry.worker_done_event.set()
        logger.info("Unified compute consumer stopped")


async def _process_compute_message(entry: _ConsumerEntry, msg: dict) -> None:
    """Apply a single message from a provider's result queue."""
    msg_type = msg.get('type')

    try:
        if msg_type == 'session_complete':
            await sync_to_async(entry.compute.apply_session_complete)(msg)
            await _handle_compute_done(msg['session_id'])

            project_id = msg.get('project_id')
            if project_id:
                entry.pending_project_ids.add(project_id)

            # First time we see this project in this run, evaluate
            # workspace auto-add patterns. ``apply_session_complete``
            # has just persisted the directory (Claude Code: from
            # JSONL body; Codex: already set at initial_sync) so
            # the pattern matching has the canonical cwd to work
            # with. Idempotent — the helper no-ops if the project
            # is already a member of every matching workspace.
            project_directory = msg.get('project_directory')
            if (
                project_id
                and project_directory
                and project_id not in entry.auto_added_project_ids
            ):
                entry.auto_added_project_ids.add(project_id)
                await auto_add_project_to_workspaces(project_id, project_directory)

            # Broadcast progress only for real sessions (not subagents)
            session_id = msg['session_id']
            if entry.display_session_ids is None or session_id in entry.display_session_ids:
                entry.completed_count += 1
                await broadcast_startup_progress(
                    "background_compute",
                    entry.completed_count,
                    entry.total_display,
                    provider=entry.ctx.provider.value,
                )

                # Every N normal sessions, broadcast project_updated for all pending projects
                entry.sessions_since_project_broadcast += 1
                if entry.sessions_since_project_broadcast >= PROJECT_BROADCAST_INTERVAL:
                    for pid in entry.pending_project_ids:
                        await _broadcast_project_updated(pid)
                    entry.pending_project_ids.clear()
                    entry.sessions_since_project_broadcast = 0

            # Accumulate affected days for batched activity recalculation
            affected_days = msg.get('affected_days')
            if project_id and affected_days:
                entry.pending_activity_days[project_id].update(
                    date_cls.fromisoformat(d) for d in affected_days
                )
                entry.sessions_since_activities_flush += 1

        elif msg_type == 'done':
            logger.info(
                f"Unified consumer: received 'done' from worker (provider={entry.ctx.provider.value})"
            )
            await _finalize_entry(entry)

        elif msg_type == 'error':
            logger.error(f"Compute error for {msg['session_id']}: {msg['error']}")

        else:
            logger.error(f"Unexpected result message type: {msg_type} => {msg}")

    except Exception as e:
        logger.error(f"Error processing result message {msg_type}: {e}", exc_info=True)

    # Flush activity recalculations every BATCH_ACTIVITY_COUNT sessions for this provider.
    try:
        if entry.sessions_since_activities_flush >= BATCH_ACTIVITY_COUNT:
            await _flush_pending_activities(entry.ctx.provider, entry.pending_activity_days)
            entry.pending_activity_days.clear()
            entry.sessions_since_activities_flush = 0
    except Exception as e:
        logger.error(f"Error flushing activity recalculations: {e}", exc_info=True)
        entry.pending_activity_days.clear()
        entry.sessions_since_activities_flush = 0


async def _finalize_entry(entry: _ConsumerEntry) -> None:
    """Flush an entry's pending broadcasts/activities and unblock its waiter.

    Idempotent: subsequent calls are no-ops thanks to :attr:`finalized`.
    """
    if entry.finalized:
        return
    entry.finalized = True

    # Final flush of project_updated broadcasts pending for this provider.
    for pid in entry.pending_project_ids:
        try:
            await _broadcast_project_updated(pid)
        except Exception as e:
            logger.error(f"Error in final project broadcast for {pid}: {e}")
    entry.pending_project_ids.clear()

    # Final flush of activity recalculations pending for this provider.
    if entry.pending_activity_days:
        try:
            await _flush_pending_activities(entry.ctx.provider, entry.pending_activity_days)
        except Exception as e:
            logger.error(f"Error in final activity flush: {e}", exc_info=True)
        entry.pending_activity_days.clear()

    entry.worker_done_event.set()


async def start_background_compute_task(ctx: ComputeContext) -> None:
    """
    Background task that processes existing sessions needing computation at startup.

    Architecture:
    - Starts a separate Process for CPU-intensive work (JSON parsing, metadata computation)
    - The worker process only READS from the database
    - All database WRITES happen in the main process via the unified consumer
      (:func:`_unified_consumer_loop`), which serializes writes across providers
      and eliminates "database is locked" errors

    This task processes all sessions with outdated or missing compute_version,
    then stops. New sessions created by the watcher get compute_version set
    at creation time, so they don't need background reprocessing.

    Progress logging: Logs progress at 10% intervals during processing.
    """

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

    # Start the worker process
    start_compute_process(ctx)

    # Register with the unified consumer. It drains every registered
    # provider's result queue from a single coroutine, so the SQLite write
    # lock is no longer contended between providers.
    worker_done_event = asyncio.Event()
    register_compute_consumer(
        ctx,
        worker_done_event,
        display_session_ids=sessions_to_display,
        total_display=total_display,
    )

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

        # Wait for the unified consumer to drain everything for this provider
        # (the worker has emitted 'done' and any pending flushes have run).
        await worker_done_event.wait()

        # Broadcast completion (using display total — sessions only, not subagents)
        await broadcast_startup_progress(
            "background_compute", total_display, total_display,
            provider=provider_value, completed=True,
        )
    finally:
        unregister_compute_consumer(ctx.provider)
        # Stop the worker process. Idempotent if it has already exited.
        stop_background_task(ctx)

    logger.info("Background compute task completed")
