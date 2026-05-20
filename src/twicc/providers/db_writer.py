"""
Unified DB writer.

A single permanent coroutine that owns every database write coming from the
two boot-time "big jobs": the per-provider initial sync (producers run in
``asyncio.to_thread`` threads) and the background metadata compute (producers
run in ``multiprocessing`` subprocesses). Both push onto process-wide shared
queues; this consumer drains them and applies every write inside
``transaction.atomic`` from one place — so the SQLite write lock is never
contended between providers, nor between the initial-sync phase and the
compute phase.

Lifecycle: started once by ``run_server`` at boot (before any provider
orchestrator starts), stopped once at application shutdown (after every
orchestrator has shut down). It strictly outlives every producer. Provider
orchestrators never start/stop it and never register anything for routing —
each message carries its own ``provider``.

Completion signalling: a producer pushes a sentinel *last*, after all its
real messages. Because the queues are FIFO, draining the sentinel proves
every preceding message of that run has been applied.
- Initial sync: ``InitialSyncDoneMarker`` carries its own ``asyncio.Event``
  (the queue is intra-process, the Event travels by reference).
- Compute: the ``done`` message comes from a subprocess and cannot carry an
  Event, so ``arm_compute_completion`` hands the orchestrator a fresh Event
  kept in ``_compute_done_events`` and set when the ``done`` is drained.

Import discipline: this module is never imported by the spawn subprocess.
Django model imports are still done lazily inside functions, matching the
convention of ``background_compute_task.py``.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import queue
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction

from twicc.core.enums import Provider
from twicc.startup_progress import broadcast_startup_progress
from twicc.workspaces import auto_add_project_to_workspaces

logger = logging.getLogger(__name__)

# Throttle: broadcast project_updated every N normal sessions during compute.
PROJECT_BROADCAST_INTERVAL = 5
# Batch size for activity recalculation flushes (per provider).
BATCH_ACTIVITY_COUNT = 50

# "spawn" context — the compute result queue is created here and passed to
# the spawn workers, so it must come from the same context they use.
_mp_ctx = multiprocessing.get_context("spawn")


# =============================================================================
# Queue payload vocabulary
# =============================================================================
#
# The consumer owns this vocabulary: every type below has a handler in
# _process_initial_sync_message. Initial-sync producers
# (providers/<p>/initial_sync.py) import these and push them; they only ever
# use the subset that applies to them. The compute side does NOT use these —
# its messages are plain orjson-encoded dicts produced by the subprocess.


class CreateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session not yet in DB.

    The consumer creates the ``Project`` (idempotent — no-op if it exists),
    saves the unsaved ``Session`` instance, bulk-creates the items, then
    persists the tracking fields. ``new_project_directory`` may be ``None``
    (Claude Code stores the cwd inside the JSONL body, not available at
    initial-sync time).
    """

    provider: Provider
    project_id: str
    new_project_directory: str | None
    new_project_stale: bool
    session: object  # an unsaved twicc.core.models.Session instance
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float


class UpdateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session already in DB.

    The consumer appends the new items (``ignore_conflicts=True`` covers the
    rare watcher-already-inserted-them race), persists the tracking fields,
    optionally clears ``stale``, and optionally resets ``compute_version``.
    """

    provider: Provider
    session: object  # an existing twicc.core.models.Session instance
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    reset_compute_version: bool
    clear_stale: bool


class MarkSessionsStalePayload(NamedTuple):
    """Producer wants to flag a set of sessions stale (no longer on disk).

    The consumer issues one ``Session.objects.filter(id__in=..., stale=False).update(stale=True)``.
    """

    provider: Provider
    session_ids: list[str]


class UpdateProjectMetadataPayload(NamedTuple):
    """End-of-sync metadata for one project.

    Each non-``None`` field is applied; ``None`` means leave it alone. Two
    boolean flags ask the consumer to invoke heavier helpers that themselves
    write to DB (``update_project_total_cost``, ``ensure_project_git_root``).
    Bundled so the project's end-of-sync writes commit in one transaction.
    """

    provider: Provider
    project_id: str
    new_sessions_count: int | None
    new_mtime: float | None
    new_stale: bool | None
    recalc_total_cost: bool
    resolve_git_root: bool
    git_root_directory: str | None


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed last by an initial-sync producer.

    Carries the ``asyncio.Event`` the consumer sets once it drains the
    marker — i.e. once every payload of that run has been applied. The
    producer awaits this same Event.
    """

    provider: Provider
    done_event: asyncio.Event


# =============================================================================
# Module state
# =============================================================================

# The two process-wide shared queues, created by start_unified_consumer().
_initial_sync_queue: queue.Queue | None = None
_compute_result_queue: object | None = None  # _mp_ctx.Queue()

# The permanent consumer task and its stop signal.
_consumer_task: asyncio.Task | None = None
_consumer_stop_event: asyncio.Event | None = None

# Compute completion: the mp.Queue cannot transport an asyncio.Event, so the
# orchestrator arms an Event here before a run and the consumer sets it when
# it drains that provider's 'done' message.
_compute_done_events: dict[Provider, asyncio.Event] = {}

# Per-provider accumulated state for the compute side only (broadcast
# throttling, batched activity flushes). The initial-sync side has no
# accumulated state — every payload is self-contained.
_compute_states: dict[Provider, "_ComputeProviderState"] = {}


@dataclass
class _ComputeProviderState:
    """Per-provider compute-run state, created by arm_compute_completion()."""

    display_session_ids: set[str] | None
    total_display: int
    pending_activity_days: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    pending_project_ids: set[str] = field(default_factory=set)
    auto_added_project_ids: set[str] = field(default_factory=set)
    sessions_since_project_broadcast: int = 0
    sessions_since_activities_flush: int = 0
    completed_count: int = 0


# =============================================================================
# Lifecycle + completion API (called by run_server and the orchestrators)
# =============================================================================


def start_unified_consumer() -> None:
    """Create the shared queues and launch the permanent consumer task.

    Called once by ``run_server`` at boot, before any provider orchestrator
    starts. Raises if called twice (programming error).
    """
    global _initial_sync_queue, _compute_result_queue
    global _consumer_stop_event, _consumer_task

    if _consumer_task is not None:
        raise RuntimeError("unified DB writer already started")

    _initial_sync_queue = queue.Queue()
    _compute_result_queue = _mp_ctx.Queue()
    _consumer_stop_event = asyncio.Event()
    _consumer_task = asyncio.create_task(_consumer_loop())
    logger.info("Unified DB writer started")


async def stop_unified_consumer() -> None:
    """Signal the consumer to stop and await it. Called once at app shutdown.

    Idempotent. Must run after every provider orchestrator has shut down, so
    no producer is still pushing.
    """
    if _consumer_stop_event is not None:
        _consumer_stop_event.set()
    if _consumer_task is not None:
        with suppress(Exception):
            await _consumer_task
    logger.info("Unified DB writer stopped")


def get_initial_sync_queue() -> queue.Queue:
    """Return the shared initial-sync queue (for an orchestrator's producer)."""
    assert _initial_sync_queue is not None, "unified DB writer not started"
    return _initial_sync_queue


def get_compute_result_queue():
    """Return the shared compute result queue (passed to a compute worker)."""
    assert _compute_result_queue is not None, "unified DB writer not started"
    return _compute_result_queue


def arm_compute_completion(
    provider: Provider,
    display_session_ids: set[str] | None,
    total_display: int,
) -> asyncio.Event:
    """Declare a compute run for ``provider`` and return its completion Event.

    Creates a fresh ``_ComputeProviderState`` and a fresh ``asyncio.Event``.
    The consumer sets the Event when it drains the ``done`` message for this
    provider (and pops the state). Overwrites any leftover state from a
    previous run — safe because the blocking ``shutdown()`` guarantees runs
    never overlap. A warning is logged if leftover state is found, which
    would mean a previous run did not finalize (lifecycle bug).
    """
    if provider in _compute_states:
        logger.warning(
            "arm_compute_completion: provider=%s still has compute state from "
            "a previous run — runs should not overlap (lifecycle bug?)",
            provider.value,
        )
    _compute_states[provider] = _ComputeProviderState(
        display_session_ids=display_session_ids,
        total_display=total_display,
    )
    event = asyncio.Event()
    _compute_done_events[provider] = event
    return event


# =============================================================================
# The consumer loop
# =============================================================================


async def _consumer_loop() -> None:
    """Drain both shared queues until the application shuts down.

    Permanent: runs from start_unified_consumer() to stop_unified_consumer().
    Resilient: an unexpected exception in one tick is logged and the loop
    continues — a single bad message must never take the writer down.
    """
    assert _consumer_stop_event is not None
    assert _initial_sync_queue is not None
    assert _compute_result_queue is not None

    logger.info("Unified DB writer loop running")
    while not _consumer_stop_event.is_set():
        try:
            any_processed = False

            # ---- Compute side (mp.Queue, shared by every provider) ----
            try:
                raw = _compute_result_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                any_processed = True
                try:
                    msg = orjson.loads(raw)
                except Exception:
                    logger.error(f"Failed to deserialize compute message: {raw!r:.500}")
                else:
                    await _process_compute_message(msg)

            # ---- Initial-sync side (queue.Queue, shared by every provider) ----
            try:
                payload = _initial_sync_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                any_processed = True
                await _process_initial_sync_message(payload)

            # Yield: tightly when busy, back off when idle.
            await asyncio.sleep(0 if any_processed else 0.05)

        except Exception as exc:
            logger.error(f"Unified DB writer tick crashed: {exc}", exc_info=True)
            await asyncio.sleep(0.05)

    logger.info("Unified DB writer loop exited")


# =============================================================================
# Compute message handling
# =============================================================================


async def _process_compute_message(msg: dict) -> None:
    """Apply one message drained from the shared compute result queue."""
    msg_type = msg.get("type")
    provider_value = msg.get("provider")
    if provider_value is None:
        logger.error(f"Compute message without 'provider': {msg_type} => {msg!r:.300}")
        return
    try:
        provider = Provider(provider_value)
    except ValueError:
        logger.error(f"Compute message with unknown provider {provider_value!r}")
        return

    if msg_type == "done":
        await _finalize_compute_run(provider)
        return

    if msg_type == "error":
        logger.error(f"Compute error for {msg.get('session_id')}: {msg.get('error')}")
        return

    if msg_type != "session_complete":
        logger.error(f"Unexpected compute message type: {msg_type} => {msg!r:.300}")
        return

    # session_complete — the heavy path.
    state = _compute_states.get(provider)
    if state is None:
        # A session_complete arrived without arm_compute_completion() having
        # run. Abnormal, but the write itself does not need state — apply it
        # and skip the broadcast/activity bookkeeping.
        logger.warning(
            "session_complete for provider=%s with no armed compute state",
            provider.value,
        )

    try:
        from twicc.providers.compute_base import BaseSessionCompute
        await sync_to_async(BaseSessionCompute.apply_session_complete)(msg)
        await _handle_compute_done(msg["session_id"])
    except Exception as e:
        logger.error(f"Error applying session_complete: {e}", exc_info=True)
        return

    if state is None:
        return

    project_id = msg.get("project_id")
    if project_id:
        state.pending_project_ids.add(project_id)

    project_directory = msg.get("project_directory")
    if project_id and project_directory and project_id not in state.auto_added_project_ids:
        state.auto_added_project_ids.add(project_id)
        await auto_add_project_to_workspaces(project_id, project_directory)

    session_id = msg["session_id"]
    if state.display_session_ids is None or session_id in state.display_session_ids:
        state.completed_count += 1
        await broadcast_startup_progress(
            "background_compute", state.completed_count, state.total_display,
            provider=provider.value,
        )
        state.sessions_since_project_broadcast += 1
        if state.sessions_since_project_broadcast >= PROJECT_BROADCAST_INTERVAL:
            for pid in state.pending_project_ids:
                await _broadcast_project_updated(pid)
            state.pending_project_ids.clear()
            state.sessions_since_project_broadcast = 0

    affected_days = msg.get("affected_days")
    if project_id and affected_days:
        state.pending_activity_days[project_id].update(
            date_cls.fromisoformat(d) for d in affected_days
        )
        state.sessions_since_activities_flush += 1

    if state.sessions_since_activities_flush >= BATCH_ACTIVITY_COUNT:
        try:
            await _flush_pending_activities(provider, state.pending_activity_days)
        except Exception as e:
            logger.error(f"Error flushing activity recalculations: {e}", exc_info=True)
        state.pending_activity_days.clear()
        state.sessions_since_activities_flush = 0


async def _finalize_compute_run(provider: Provider) -> None:
    """Drain-time finalisation when a provider's compute worker is done.

    Flushes the provider's pending broadcasts + activities, sets its
    completion Event, and drops its state so the next run starts clean.
    """
    logger.info(f"Unified DB writer: compute 'done' for provider={provider.value}")
    state = _compute_states.pop(provider, None)
    if state is not None:
        for pid in state.pending_project_ids:
            try:
                await _broadcast_project_updated(pid)
            except Exception as e:
                logger.error(f"Error in final project broadcast for {pid}: {e}")
        if state.pending_activity_days:
            try:
                await _flush_pending_activities(provider, state.pending_activity_days)
            except Exception as e:
                logger.error(f"Error in final activity flush: {e}", exc_info=True)

    event = _compute_done_events.pop(provider, None)
    if event is not None:
        event.set()
    else:
        logger.warning(
            "compute 'done' for provider=%s with no armed completion event",
            provider.value,
        )


async def _handle_compute_done(session_id: str) -> None:
    """Broadcast session_updated for a real, user-visible session."""
    from twicc.core.models import Session, SessionType
    from twicc.core.serializers import serialize_session

    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
        if session.user_message_count == 0 or session.type != SessionType.SESSION:
            return
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            }},
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
                {"type": "broadcast", "data": {
                    "type": "project_updated",
                    "project": serialize_project(project),
                }},
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
# Initial-sync message handling
# =============================================================================


async def _process_initial_sync_message(msg) -> None:
    """Apply one message drained from the shared initial-sync queue."""
    try:
        if isinstance(msg, CreateSessionPayload):
            await sync_to_async(_apply_create_session_payload)(msg)
        elif isinstance(msg, UpdateSessionPayload):
            await sync_to_async(_apply_update_session_payload)(msg)
        elif isinstance(msg, MarkSessionsStalePayload):
            await sync_to_async(_apply_mark_sessions_stale_payload)(msg)
        elif isinstance(msg, UpdateProjectMetadataPayload):
            await sync_to_async(_apply_update_project_metadata_payload)(msg)
        elif isinstance(msg, InitialSyncDoneMarker):
            logger.info(
                f"Unified DB writer: initial-sync 'done' for provider={msg.provider.value}"
            )
            msg.done_event.set()
        else:
            logger.error(
                f"Unexpected initial-sync message type: {type(msg).__name__} => {msg!r:.300}"
            )
    except Exception as e:
        logger.error(
            f"Error processing initial-sync message {type(msg).__name__}: {e}",
            exc_info=True,
        )


def _apply_create_session_payload(payload: CreateSessionPayload) -> None:
    """Persist a new session (and project if missing) plus its items, atomically."""
    from twicc.core.models import SessionItem
    from twicc.projects import register_project_sync

    with transaction.atomic():
        register_project_sync(
            payload.project_id,
            directory=payload.new_project_directory,
            stale=payload.new_project_stale,
        )
        payload.session.save()
        if payload.items:
            SessionItem.objects.bulk_create(
                [SessionItem(session=payload.session, line_num=ln, content=ct)
                 for ln, ct in payload.items],
                ignore_conflicts=True, batch_size=50,
            )
        payload.session.last_offset = payload.last_offset
        payload.session.last_line = payload.last_line
        payload.session.mtime = payload.mtime
        payload.session.save(update_fields=["last_offset", "last_line", "mtime"])


def _apply_update_session_payload(payload: UpdateSessionPayload) -> None:
    """Append items to an existing session and update tracking fields, atomically."""
    from twicc.core.models import SessionItem

    with transaction.atomic():
        if payload.items:
            SessionItem.objects.bulk_create(
                [SessionItem(session=payload.session, line_num=ln, content=ct)
                 for ln, ct in payload.items],
                ignore_conflicts=True, batch_size=50,
            )
        update_fields = ["last_offset", "last_line", "mtime"]
        payload.session.last_offset = payload.last_offset
        payload.session.last_line = payload.last_line
        payload.session.mtime = payload.mtime
        if payload.clear_stale and payload.session.stale:
            payload.session.stale = False
            update_fields.append("stale")
        if payload.reset_compute_version and payload.session.compute_version is not None:
            payload.session.compute_version = None
            update_fields.append("compute_version")
        payload.session.save(update_fields=update_fields)


def _apply_mark_sessions_stale_payload(payload: MarkSessionsStalePayload) -> None:
    """Mark a batch of sessions stale via a single bulk UPDATE."""
    from twicc.core.models import Session

    if not payload.session_ids:
        return
    with transaction.atomic():
        Session.objects.filter(id__in=payload.session_ids, stale=False).update(stale=True)


def _apply_update_project_metadata_payload(payload: UpdateProjectMetadataPayload) -> None:
    """Apply end-of-sync metadata updates for one project, atomically."""
    from twicc.core.models import Project
    from twicc.projects import ensure_project_git_root, update_project_total_cost

    with transaction.atomic():
        if (payload.new_sessions_count is not None
                or payload.new_mtime is not None
                or payload.new_stale is not None):
            try:
                project = Project.objects.get(id=payload.project_id)
            except Project.DoesNotExist:
                logger.warning(
                    f"UpdateProjectMetadataPayload: project {payload.project_id} not found"
                )
                project = None
            if project is not None:
                update_fields: list[str] = []
                if payload.new_sessions_count is not None:
                    project.sessions_count = payload.new_sessions_count
                    update_fields.append("sessions_count")
                if payload.new_mtime is not None:
                    project.mtime = payload.new_mtime
                    update_fields.append("mtime")
                if payload.new_stale is not None:
                    project.stale = payload.new_stale
                    update_fields.append("stale")
                if update_fields:
                    project.save(update_fields=update_fields)

        if payload.recalc_total_cost:
            update_project_total_cost(payload.project_id)
        if payload.resolve_git_root and payload.git_root_directory:
            ensure_project_git_root(payload.project_id, payload.git_root_directory)
