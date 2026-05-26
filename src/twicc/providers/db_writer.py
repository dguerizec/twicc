"""
DB writer.

A single permanent coroutine that owns every database write coming from the
two boot-time "big jobs": the per-provider initial sync (producers run in
``asyncio.to_thread`` threads) and the background metadata compute (producers
run in ``multiprocessing`` subprocesses). Both push onto process-wide shared
queues; this DB writer drains them and applies every write inside
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
every preceding message of that run has been applied. The completion carries
the run's failure count so the producer learns the run was not clean.
- Initial sync: ``InitialSyncDoneMarker`` carries its own ``asyncio.Future``
  (the queue is intra-process, the Future travels by reference); the DB writer
  resolves it with the count of payloads that failed to apply.
- Compute: the ``done`` message comes from a subprocess and cannot carry a
  Future, so ``arm_compute_completion`` hands the orchestrator a fresh Future
  kept in ``_compute_done_events``, resolved with the failed-session count
  when the ``done`` is drained.

Import discipline: this module is never imported by the spawn subprocess.
Django model imports are still done lazily inside functions, matching the
convention of ``background_compute_task.py``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import multiprocessing
import queue
import threading
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
from twicc.logging_context import provider_log_context
from twicc.startup_progress import broadcast_startup_progress
from twicc.workspaces import auto_add_project_to_workspaces

logger = logging.getLogger(__name__)

# Throttle: broadcast project_updated every N normal sessions during compute.
PROJECT_BROADCAST_INTERVAL = 5
# Batch size for activity recalculation flushes (per provider).
BATCH_ACTIVITY_COUNT = 50

# Bounded-queue capacities. Both boot-time producers (initial-sync threads,
# compute subprocesses) can parse far faster than the DB writer commits to
# SQLite. An unbounded queue would let the backlog — each initial-sync payload
# carrying a whole session's JSONL lines — grow to gigabytes of RAM. A bounded
# queue makes a full queue block the producer instead: the backlog is capped,
# and the producer is throttled to the DB writer's write rate.
INITIAL_SYNC_QUEUE_MAXSIZE = 200
COMPUTE_QUEUE_MAXSIZE = 200

# "spawn" context — the compute result queue is created here and passed to
# the spawn workers, so it must come from the same context they use.
_mp_ctx = multiprocessing.get_context("spawn")


# =============================================================================
# Queue payload vocabulary
# =============================================================================
#
# The DB writer owns this vocabulary: every type below has a handler in
# _process_thread_message. Initial-sync producers
# (providers/<p>/initial_sync.py) import these and push them; they only ever
# use the subset that applies to them. The compute side does NOT use these —
# its messages are plain orjson-encoded dicts produced by the subprocess.


class CreateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session not yet in DB.

    The DB writer creates the ``Project`` (idempotent — no-op if it exists),
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

    The DB writer appends the new items (``ignore_conflicts=True`` covers the
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

    The DB writer issues one ``Session.objects.filter(id__in=..., stale=False).update(stale=True)``.
    """

    provider: Provider
    session_ids: list[str]


class UpdateProjectMetadataPayload(NamedTuple):
    """End-of-sync metadata for one project.

    ``new_stale`` is applied when non-``None``. Four boolean flags ask the
    DB writer to do work that reads back from the DB: ``recalc_sessions_count``
    and ``recalc_mtime`` recompute those ``Project`` fields from the project's
    sessions; ``recalc_total_cost`` and ``resolve_git_root`` invoke heavier
    helpers that themselves write to DB. Bundled so the project's end-of-sync
    writes commit in one transaction.

    ``recalc_sessions_count`` / ``recalc_mtime`` exist because neither value
    can be computed in the producer: both are cross-provider, so a value
    counted in one provider's producer could clobber — or be clobbered by — a
    fresher value another provider's compute writes; reading them from the DB
    in the producer also races the DB writer, which has not yet applied this
    run's session payloads. The DB writer recomputes them instead, inside the
    same transaction that applies the payload.
    """

    provider: Provider
    project_id: str
    recalc_sessions_count: bool
    recalc_mtime: bool
    new_stale: bool | None
    recalc_total_cost: bool
    resolve_git_root: bool
    git_root_directory: str | None


class ResolveProjectGitRootsPayload(NamedTuple):
    """End-of-sync request to resolve git_root for every project on disk.

    A provider enqueues this once, last in its run, instead of running
    ``Project.objects.filter(directory__isnull=False, stale=False)`` itself
    and pushing one payload per project: that producer-side query runs
    before the DB writer has applied this run's earlier project payloads
    (FIFO), so a project being un-staled in the same sync would still look
    stale and be skipped. The DB writer runs the query when it drains this
    marker — after every prior project update of the run has committed.
    """

    provider: Provider


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed last by an initial-sync producer.

    Carries an ``asyncio.Future`` the DB writer resolves once it drains the
    marker — i.e. once every payload of that run has been applied. The Future
    resolves to the count of payloads that failed to apply, so the producer
    (which awaits it) learns whether the run was clean.
    """

    provider: Provider
    done_future: asyncio.Future


# =============================================================================
# Module state
# =============================================================================

# The two process-wide shared queues, created by start_db_writer().
_thread_queue: queue.Queue | None = None
_subprocess_queue: object | None = None  # _mp_ctx.Queue()

# The permanent DB writer task and its stop signal.
_db_writer_task: asyncio.Task | None = None
_db_writer_stop_event: asyncio.Event | None = None

# Intra-process queue of finalization jobs the DB writer must run itself, so
# their writes are serialized with every other DB writer write — the
# single-writer guarantee. abandon_compute_run() runs in a provider's
# shutdown() task, not in the DB writer task, and uses this to have the
# DB writer flush an abandoned compute run's batched state instead of writing
# to the DB directly and racing the DB writer.
_async_queue: "asyncio.Queue | None" = None

# Shared write lock — serializes every SQLite-writing critical section that
# happens in the main process. The DB writer itself acquires it via
# :func:`run_under_db_write_lock` around each of the three queue branches in
# ``_drain_one``; external callers (the JSONL watcher, agent lifecycle hooks,
# the cron expiry monitor, the Codex title flush — every direct writer R19
# left in place) will eventually adopt either :func:`run_under_db_write_lock`
# (cancellation-safe, the recommended path for anything that awaits
# ``sync_to_async``) or, for short critical sections that do not await
# orphanable executor work, the raw lock via :func:`get_db_write_lock`.
# The point is to move the contention from SQLite's busy_timeout (no fairness,
# 30 s grace period, raises "database is locked" on loss) into asyncio's
# FIFO lock (fair, no timeout, never raises). ``None`` outside of an active
# DB writer lifetime so a stray ``get_db_write_lock`` call surfaces a clear
# RuntimeError instead of silently handing out a stale Lock.
_db_write_lock: asyncio.Lock | None = None

# Compute completion is keyed by a per-run id, not by provider: a cancelled
# run's worker can still have stale messages in the shared queue when a
# hot-restarted run is armed, and the run_id keeps each run's state and
# completion Future isolated. arm_compute_completion() mints the ids.
_compute_run_id_seq = itertools.count(1)

# run_id -> the completion Future the orchestrator awaits. The mp.Queue cannot
# transport a Future, so arm_compute_completion() keeps one here and the
# DB writer resolves it (with the run's failed-session count) when it drains
# that run's 'done' message.
_compute_done_events: dict[int, asyncio.Future] = {}

# run_id -> that run's accumulated compute state (broadcast throttling,
# batched activity flushes, failure tally). Initial-sync payloads are
# self-contained; the only initial-sync run state is the failure tally below.
_compute_states: dict[int, "_ComputeProviderState"] = {}

# Per-provider failed-payload counter for the in-flight initial-sync run.
# Incremented when an _apply_* raises; reported to the orchestrator via the
# InitialSyncDoneMarker's Future and reset when that marker is drained.
_initial_sync_failures: dict[Provider, int] = defaultdict(int)

# Per-provider count of subagents skipped because their parent row was absent
# (orphan-parent guard). A deliberate skip, distinct from a failure, but
# surfaced in the run summary so it is never silently lost.
_initial_sync_orphan_skips: dict[Provider, int] = defaultdict(int)


@dataclass
class _ComputeProviderState:
    """One compute run's accumulated state, created by arm_compute_completion().

    Keyed in ``_compute_states`` by ``run_id``; ``provider`` is kept for
    logging and so arm_compute_completion() can drop a previous run's leftover
    state for the same provider.

    ``abandoned`` is set by :func:`abandon_compute_run` when a provider shuts
    down: once set, the DB writer skips every remaining ``session_complete`` for
    the run, and the run's batched state is flushed and dropped by the
    :class:`_AbandonComputeRunJob` the DB writer runs.
    """

    provider: Provider
    run_id: int
    display_session_ids: set[str] | None
    total_display: int
    pending_activity_days: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    pending_project_ids: set[str] = field(default_factory=set)
    auto_added_project_ids: set[str] = field(default_factory=set)
    sessions_since_project_broadcast: int = 0
    sessions_since_activities_flush: int = 0
    completed_count: int = 0
    failed_count: int = 0
    abandoned: bool = False


class _AbandonComputeRunJob(NamedTuple):
    """A request for the DB writer to finalize an abandoned compute run.

    Pushed onto ``_async_queue`` by :func:`abandon_compute_run` (which runs
    in a provider's ``shutdown()`` task) and executed by the DB writer task
    itself, so the run's batched activity-recalculation flush is serialized
    with every other DB writer write. ``done`` is resolved once the DB writer
    has finalized and dropped the run, letting ``shutdown()`` move on.
    """

    run_id: int
    provider: Provider
    done: asyncio.Future


# -----------------------------------------------------------------------------
# Generic DB writer jobs (periodic tasks routed through the DB writer)
# -----------------------------------------------------------------------------
#
# These are pushed onto ``_async_queue`` by an async producer (a periodic
# task: commands sync, usage sync, model retirement, pricing) via
# :func:`submit_async_job`. The producer prepares its payload — HTTP fetch,
# filesystem scan, parsing — entirely outside the DB writer; only the actual DB
# write (and, for commands, the SELECT-current diff that precedes it) runs in
# the DB writer, in one ``transaction.atomic``. The job carries an
# ``asyncio.Future`` the DB writer settles with the result (or the exception),
# so the producer awaits the apply just like any other DB-writer-routed work.
#
# Boot-time motivation: the periodic tasks' *first* iteration fires immediately
# when their provider's orchestrator starts, in parallel with the initial-sync
# producer the DB writer is already draining. Two concurrent connections then
# race for SQLite's WAL write lock and trip "database is locked" errors. By
# routing them through the DB writer we restore the single-writer guarantee.


class _ApplyDesiredCommandsJob(NamedTuple):
    """A request to reconcile ``Command`` rows for one ``(provider, activation_char)`` scope.

    The producer (each provider's ``commands_task``) builds ``desired`` from
    its own discovery source — filesystem scan for Claude Code, ``skills/list``
    JSON-RPC for Codex — and submits this job. The DB writer reads the current
    rows from DB, computes the diff, and applies create/update/delete in one
    ``transaction.atomic``. ``future`` resolves to the same stats dict the old
    ``apply_desired_commands`` helper returned.
    """

    provider: Provider
    activation_char: str
    desired: dict[tuple[str | None, str], dict]
    future: asyncio.Future  # → dict[str, int] with keys created/updated/deleted/unchanged


class _CreateUsageSnapshotJob(NamedTuple):
    """A request to insert a new ``UsageSnapshot`` row.

    The producer (each provider's ``usage_task``) calls its ``fetch_usage``,
    parses the API response into the ``UsageSnapshot`` column fields, and
    submits this job. The DB writer issues one ``UsageSnapshot.objects.create``.
    ``future`` resolves to the created ``UsageSnapshot`` instance so the
    producer can log its values.
    """

    provider: Provider
    fields: dict
    future: asyncio.Future  # → UsageSnapshot


class _RetireSessionsJob(NamedTuple):
    """A request to apply per-session retirement-driven field updates.

    The producer (``model_retirement_task``) iterates the active agent
    managers, decides which sessions need their ``selected_model`` (and
    possibly ``effort``) upgraded, and submits the resulting
    ``{session_id: field_updates}`` map. The DB writer applies them in one
    ``transaction.atomic`` via ``Session.objects.filter(id=sid).update(**upd)``
    per session. ``future`` resolves to the number of sessions touched.
    """

    provider: Provider
    updates: dict[str, dict[str, object]]
    future: asyncio.Future  # → int (count of sessions updated)


class _PersistProviderPricesJob(NamedTuple):
    """A request to upsert OpenRouter prices for one provider.

    The producer (``pricing_task``) calls ``fetch_openrouter_models`` and
    ``extract_provider_prices`` (HTTP + parse, no DB), then submits this job.
    The DB writer applies the diff in one ``transaction.atomic``: for each row,
    SELECT the latest, INSERT a new history row only if anything actually
    changed. Invalidates the in-process price cache when at least one row was
    created. ``future`` resolves to a ``{"created": N, "unchanged": M}`` dict.
    """

    provider: Provider
    prices: list[dict]
    future: asyncio.Future  # → dict[str, int] with keys created/unchanged


class _MarkSessionsIndexedJob(NamedTuple):
    """A request to bump ``search_version`` on a batch of just-indexed sessions.

    Cross-provider job: the producer (``search_indexing_task``) sweeps every
    session needing indexing regardless of owning provider, in ``-mtime``
    order, and accumulates session ids into batches of up to 50 (or up to 2 s
    worth of slower indexing) before submitting one job for the batch. The
    DB writer applies a single
    ``Session.objects.filter(id__in=...).update(search_version=...)`` per job,
    centralizing what used to be one direct ``session.save()`` per indexed
    session — the producer was the last non-watcher, non-HTTP/WS writer left
    in the runtime loop. ``provider`` is ``None`` because the batch mixes
    sessions of every backend.
    """

    session_ids: list[str]
    future: asyncio.Future  # → int (count of sessions actually updated)
    provider: Provider | None = None


# =============================================================================
# Lifecycle + completion API (called by run_server and the orchestrators)
# =============================================================================


def start_db_writer() -> None:
    """Create the shared queues and launch the permanent DB writer task.

    Called once by ``run_server`` at boot, before any provider orchestrator
    starts. Raises if called twice (programming error).
    """
    global _thread_queue, _subprocess_queue
    global _db_writer_stop_event, _db_writer_task, _async_queue
    global _db_write_lock

    if _db_writer_task is not None:
        raise RuntimeError("DB writer already started")

    _thread_queue = queue.Queue(maxsize=INITIAL_SYNC_QUEUE_MAXSIZE)
    _subprocess_queue = _mp_ctx.Queue(maxsize=COMPUTE_QUEUE_MAXSIZE)
    _async_queue = asyncio.Queue()
    _db_writer_stop_event = asyncio.Event()
    # Created here, not at module level. ``asyncio.Lock`` itself binds to the
    # running loop lazily (on first contended acquire) in 3.11+, so any timing
    # would technically work for binding alone, but instantiating it inside
    # ``start_db_writer`` co-locates the lock's lifetime with the writer
    # task's: "DB writer running ⇔ lock available" is obvious from the
    # lifecycle code, and a stop properly clears the slot (see
    # ``stop_db_writer``).
    _db_write_lock = asyncio.Lock()
    _db_writer_task = asyncio.create_task(_db_writer_loop())
    logger.info("DB writer started")


async def stop_db_writer() -> None:
    """Signal the DB writer to stop and await it. Called once at app shutdown.

    Idempotent. Must run after every provider orchestrator has shut down, so
    no producer is still pushing.

    Multi-cancel safe: the writer task's final drain (the
    ``while True: await _drain_one()`` loop inside :func:`_db_writer_loop`
    after the stop event fires) is what guarantees no queued message is
    abandoned, so we MUST wait for the task to actually finish — even if
    our own coroutine is cancelled mid-await. A naive
    ``await _db_writer_task`` would let asyncio propagate our cancellation
    into the writer task via ``Task._fut_waiter``, interrupting the final
    drain and contradicting the "lock cleared ⇒ drain complete" contract.

    The shielded while-loop is identical in spirit to
    :func:`run_under_db_write_lock`: every outer cancel raises
    ``CancelledError`` on a fresh ``await asyncio.shield(_db_writer_task)``,
    we remember it, and re-enter — only exiting once
    ``_db_writer_task.done()`` is True. Then we clear the lock reference
    and re-raise ``CancelledError`` so the rest of the shutdown path
    proceeds.
    """
    global _db_write_lock

    if _db_writer_stop_event is not None:
        _db_writer_stop_event.set()
    cancelled = False
    try:
        if _db_writer_task is not None:
            while not _db_writer_task.done():
                try:
                    # Shielded so outer cancels never propagate into the
                    # writer task (which would interrupt the final drain).
                    await asyncio.shield(_db_writer_task)
                except asyncio.CancelledError:
                    cancelled = True
                    # Loop and keep waiting.
            # Task is done — retrieve any exception so asyncio doesn't log
            # "exception was never retrieved". The writer loop catches
            # everything internally, but the helper guard is cheap.
            with suppress(BaseException):
                _db_writer_task.result()
    finally:
        # Release the write-lock reference once the writer task is fully
        # done — both the steady-state ``while not stop_event:`` loop AND
        # the final drain inside :func:`_db_writer_loop` are now complete,
        # so no apply still needs the lock.
        _db_write_lock = None
    if cancelled:
        # Our caller wanted us cancelled; honour that AFTER the writer task
        # has actually drained and we've cleared module state.
        raise asyncio.CancelledError()
    logger.info("DB writer stopped")


def get_db_write_lock() -> asyncio.Lock:
    """Return the shared write lock for SQLite serialization.

    .. warning::

       **Most callers should use :func:`run_under_db_write_lock` instead.**
       The naive pattern ``async with get_db_write_lock(): await
       sync_to_async(fn)(...)`` is cancellation-unsafe: if the caller is
       cancelled while waiting for the executor thread, ``__aexit__``
       releases the lock the instant the await unwinds — even though the
       worker thread is still inside ``transaction.atomic`` — letting
       the next writer acquire and run a concurrent SQLite UPDATE.
       :func:`run_under_db_write_lock` wraps the apply in a shielded
       inner Task and waits across N outer cancellations, so the lock
       stays held until the worker thread has actually returned.

       Direct ``async with get_db_write_lock()`` is only safe when the
       critical section does NOT ``await`` anything that could be
       orphaned by cancellation (no ``sync_to_async``, no executor
       call, no nested ``await`` on a third-party Future). For
       everything else, prefer the runner.

    The lock is the single FIFO serialisation point shared with every
    other main-process SQLite writer — current (the DB writer's own
    apply branches inside :func:`_drain_one`) AND future (the JSONL
    watcher, agent lifecycle hooks, the cron expiry monitor, the
    Codex title flush, the ``PendingSessionsWatcher`` — every path
    R19 documented as still bypassing the writer queue). The point
    is to move contention from SQLite's ``busy_timeout`` (no
    fairness, gives up after 30 s, raises ``database is locked``)
    into asyncio's FIFO ``Lock`` (fair, never raises).

    External wiring (watcher etc.) is intentionally deferred to a
    later commit so the lock infrastructure can land and stabilise
    first.

    Raises :class:`RuntimeError` if the DB writer is not started (or
    has been stopped) — handing out a stale ``None`` would silently
    let the caller's ``async with`` succeed on whatever the call site
    happens to cache, breaking serialisation.
    """
    if _db_write_lock is None:
        raise RuntimeError("DB writer not started")
    return _db_write_lock


def get_thread_queue() -> queue.Queue:
    """Return the shared thread queue (for an orchestrator's producer)."""
    assert _thread_queue is not None, "DB writer not started"
    return _thread_queue


async def put_thread_message(
    item: object, stop_event: threading.Event | None = None
) -> bool:
    """Push a payload (initial-sync entry or completion marker) onto the thread queue.

    For event-loop callers (the orchestrators). The shared queue is bounded;
    a full queue is handled by polling ``put_nowait()`` and awaiting a short
    sleep between attempts — entirely on the event loop, with no worker
    thread. A blocking put offloaded to a thread (``asyncio.to_thread``) is
    not cancellation-safe: if the awaiting coroutine is cancelled at shutdown,
    the thread's put keeps running and can enqueue the item *afterwards* —
    behind the drain marker ``shutdown()`` already pushed and awaited — which
    breaks the FIFO drain proof and can interleave with a hot restart. Polling
    keeps the whole wait inside the coroutine, so cancellation enqueues
    nothing: ``put_nowait()`` is atomic, and a cancel can only land on the
    ``await asyncio.sleep`` between attempts.

    With a ``stop_event``: returns ``True`` once the item is enqueued,
    ``False`` if ``stop_event`` fired first — the item is dropped, which is
    safe at shutdown since nothing awaits its completion then. ``stop_event``
    is rechecked before every attempt.

    With ``stop_event=None``: the item is never dropped — the loop retries
    until the put succeeds, then returns ``True``. Used for the drain marker
    ``shutdown()`` pushes once ``_sync_stop_event`` is already set: that marker
    proves the queue is drained and must not be dropped. The DB writer is
    permanent (it outlives every provider shutdown), so it keeps draining and
    a slot always frees up.
    """
    q = get_thread_queue()
    while stop_event is None or not stop_event.is_set():
        try:
            q.put_nowait(item)
            return True
        except queue.Full:
            await asyncio.sleep(0.05)
    return False


def get_subprocess_queue():
    """Return the shared compute result queue (passed to a compute worker)."""
    assert _subprocess_queue is not None, "DB writer not started"
    return _subprocess_queue


def arm_compute_completion(
    provider: Provider,
    display_session_ids: set[str] | None,
    total_display: int,
) -> tuple[int, asyncio.Future]:
    """Declare a compute run for ``provider``; return ``(run_id, Future)``.

    Mints a fresh ``run_id`` and creates this run's ``_ComputeProviderState``
    and completion ``asyncio.Future``, both keyed by ``run_id``. The worker is
    spawned with this ``run_id`` and tags every message with it, so the
    DB writer routes each message to its own run — a stale message from a
    cancelled run can never touch a hot-restarted one. The Future resolves to
    the run's failed-session count when its ``done`` message is drained.

    Any leftover state for the same provider (a previous run whose worker was
    force-killed before emitting ``done``) is dropped here, with a warning —
    that state would otherwise leak for the process lifetime.
    """
    run_id = next(_compute_run_id_seq)
    for stale_run_id in [rid for rid, st in _compute_states.items() if st.provider == provider]:
        logger.warning(
            "arm_compute_completion: dropping leftover compute state for "
            "run_id=%d — its run never finalized (force-killed worker?)",
            stale_run_id,
        )
        _compute_states.pop(stale_run_id, None)
        _compute_done_events.pop(stale_run_id, None)
    _compute_states[run_id] = _ComputeProviderState(
        provider=provider,
        run_id=run_id,
        display_session_ids=display_session_ids,
        total_display=total_display,
    )
    future = asyncio.get_running_loop().create_future()
    _compute_done_events[run_id] = future
    return run_id, future


async def abandon_compute_run(run_id: int, provider: Provider) -> None:
    """Abandon a compute run at provider shutdown and flush its batched state.

    Awaited by a provider orchestrator's ``shutdown()`` once it has decided to
    stop the compute worker. Two things happen:

    1. The run is flagged ``abandoned`` synchronously, before any ``await``.
       From here on the DB writer skips every ``session_complete`` still queued
       (or still incoming) for this ``run_id`` — a shut-down provider's partial
       compute results must never apply after the provider has stopped, where
       they could clobber a hot-restart's fresher state (and leave freshly
       synced lines with ``compute_version`` already current). The run's
       sessions are recomputed from scratch on the next start.

    2. Finalization is handed to the DB writer task via an
       :class:`_AbandonComputeRunJob` on ``_async_queue``. The DB writer
       processes one message at a time, so by the time it runs the job any
       in-flight ``session_complete`` apply — and its post-apply bookkeeping
       that accumulates the session's affected activity days — has fully
       finished. The job flushes the run's batched project broadcasts and
       activity recalculations for the sessions applied before the abandon,
       then drops the run state. The flush runs *in the DB writer*, never as a
       direct DB write from this ``shutdown()`` task, so the single-writer
       guarantee holds. This coroutine awaits the job, so the provider does
       not reach STOPPED — and a hot restart cannot begin — until the
       finalization has committed.

    No-op when ``run_id`` is untracked or its tracked state belongs to another
    provider — e.g. the ``ComputeContext`` default ``run_id=0`` when the worker
    was torn down before :func:`arm_compute_completion` ran, or a run whose
    worker already emitted ``done`` and was finalized by
    :func:`_finalize_compute_run`.
    """
    state = _compute_states.get(run_id)
    if state is None or state.provider != provider:
        return
    # Flag synchronously, before any await: every session_complete the
    # DB writer dequeues for this run from now on is skipped.
    state.abandoned = True
    assert _async_queue is not None, "DB writer not started"
    done = asyncio.get_running_loop().create_future()
    _async_queue.put_nowait(
        _AbandonComputeRunJob(run_id=run_id, provider=provider, done=done)
    )
    # Await the DB writer-side finalization so shutdown does not reach STOPPED
    # before the run's batched state has been flushed and committed.
    with suppress(Exception):
        await done


def enqueue_async_job(job) -> None:
    """Queue an async-queue job for the DB writer without awaiting its result.

    Synchronous shape of :func:`submit_async_job`: the caller keeps the
    ``job.future`` reference and decides when (if ever) to await it. Useful
    for producers that batch many small jobs and want to keep submitting
    while previous batches are still being applied — most notably the
    cross-provider search-indexing sweep, which would otherwise serialize
    one batch's apply between successive indexed-session loops (the size
    of the batch becomes a latency multiplier, defeating the point of
    batching).

    Same lifecycle guards as :func:`submit_async_job`: raises ``RuntimeError``
    if the writer is not started or has been signalled to stop. The caller
    is responsible for settling ``job.future`` (with an exception, typically)
    if this raises — the DB writer will never see the job in that case, and
    nothing else would otherwise complete the Future.
    """
    if _db_writer_task is None or _db_writer_stop_event is None or _async_queue is None:
        raise RuntimeError("DB writer not started")
    if _db_writer_stop_event.is_set():
        raise RuntimeError("DB writer is stopping")
    _async_queue.put_nowait(job)


async def submit_async_job(job) -> object:
    """Push a periodic-task job onto the DB writer queue and await its result.

    Used by the periodic-task producers (commands sync, usage sync, model
    retirement, pricing) to route their DB writes through the DB writer.
    The caller pre-creates ``job.future`` (typically with
    ``loop.create_future()``) so this function can stay tiny and the job's
    NamedTuple is fully immutable. The DB writer settles ``job.future`` with
    the apply's return value, or with the apply's exception so the producer
    sees the failure rather than silently dropping it.

    Raises ``RuntimeError`` if the writer is not started or has been signalled
    to stop. A producer that has just received its task-level stop event
    should check it before calling this — the periodic loops do so via their
    own ``stop_event.is_set()`` check, and the DB writer outlives them all
    (the global shutdown stops every orchestrator before the writer, so a
    live periodic task always has a live writer to talk to).
    """
    enqueue_async_job(job)
    # ``asyncio.shield`` guards ``job.future`` against producer-task cancellation.
    # asyncio normally cancels the Future a cancelled Task is awaiting (via
    # ``Task._fut_waiter``); without the shield, ``job.future.cancelled()`` would
    # then be True by the time the DB writer reaches ``_settle_async_job``, and
    # its ``if not job.future.done(): set_result/set_exception`` guard would
    # silently drop the apply's outcome. With the shield, a CancelledError still
    # propagates out of this ``await`` to the caller (so a hot-toggle / shutdown
    # cancel still tears down promptly), but the future itself stays settleable
    # — the DB writer's apply is always observed, even if no producer is left
    # awaiting it.
    return await asyncio.shield(job.future)


# =============================================================================
# The DB writer loop
# =============================================================================


async def run_under_db_write_lock(coro_factory) -> None:
    """Run a coroutine under the shared DB write lock, cancellation-safe.

    Public, recommended API for every external caller that needs to wrap
    a ``transaction.atomic`` block in the DB writer's serialization. Use
    it instead of ``async with get_db_write_lock(): ...`` whenever the
    critical section ``await``s something that dispatches to an executor
    thread (typically ``sync_to_async`` around Django ORM code) — the
    raw-lock pattern releases the lock as soon as the await is cancelled,
    even though the worker thread is still inside its transaction.

    ``coro_factory`` is a **zero-arg callable** that returns a fresh
    coroutine — typically ``lambda: my_async_fn(arg1, arg2)`` or a
    nested ``async def``. The factory is invoked AFTER the lock is
    acquired, NOT before: passing an already-created coroutine would
    leave it unawaited (with the runtime warning + lost work) if our
    task were cancelled while waiting for the lock.

    Cancellation-safety details:

    - The lock is acquired via ``async with``. If we are cancelled while
      waiting to acquire, no coroutine has been created yet, so nothing
      is leaked.
    - Once acquired, we instantiate the inner Task and shield it.
      ``await asyncio.shield(inner)`` raises ``CancelledError`` on our
      side if we are cancelled, but the shield keeps the inner alive.
      We catch the cancel, set a flag, and loop — every subsequent
      cancel (signal-handler escalation, forced shutdown re-cancelling
      tasks that haven't died yet) raises here again on the fresh
      shielded await. The loop only exits once ``inner.done()`` is
      True, which means the executor thread has actually returned.
    - Inner exceptions: any ``Exception`` (non-cancel) raised by the
      inner is re-raised by ``await asyncio.shield(inner)``, propagates
      through the ``async with``, and is handled by the caller's own
      ``try/except``. We don't catch them here.
    - On any cancelled path, ``CancelledError`` is re-raised at the
      end so the caller's teardown proceeds normally — but only after
      the lock has been released by ``__aexit__`` and ``inner`` has
      truly finished.

    The companion :func:`get_db_write_lock` returns the raw lock for
    advanced callers whose critical section does not await on
    orphanable work (no executor calls); see its docstring for the
    warning.
    """
    assert _db_write_lock is not None
    async with _db_write_lock:
        # Create the coroutine AFTER we hold the lock. If a cancellation
        # arrives while waiting for ``async with``, no coroutine has been
        # created yet — no "coroutine was never awaited" warning, no lost
        # dequeued work.
        inner = asyncio.create_task(coro_factory())
        cancelled = False
        while not inner.done():
            try:
                # Always shielded — outer cancels never reach the inner Task.
                await asyncio.shield(inner)
            except asyncio.CancelledError:
                # Outer cancelled. Loop and re-await; the shield kept the
                # inner alive. Each subsequent ``cancel()`` will raise here
                # again on the fresh await, and we'll loop again until
                # ``inner.done()``.
                cancelled = True
        # ``inner.done()`` is True. Drain any inner exception so asyncio
        # doesn't log "Task exception was never retrieved" — the caller's
        # ``try/except Exception`` around our entry point would normally
        # see it via the shield's await re-raise, but if we got here
        # through the ``cancelled`` path the await never returned a value.
        with suppress(BaseException):
            inner.result()
        if cancelled:
            # Re-raise so the rest of the caller's teardown can proceed.
            # The lock is released by ``__aexit__`` just below.
            raise asyncio.CancelledError()


async def _drain_one() -> bool:
    """Process at most one message from each queue.

    Covers the two shared producer queues (compute, initial-sync) and the
    intra-process async queue. Returns True if anything was processed
    this call.

    Every per-message apply runs inside :func:`provider_log_context` so
    log records emitted during the apply (including those bubbling up
    through ``sync_to_async`` and the helpers it transitively calls) get
    the right ``provider`` tag in the log column — the DB writer task
    itself runs in the global context, but each individual message
    belongs to one provider.

    Each of the three branches dispatches its apply through
    :func:`run_under_db_write_lock`, which acquires :data:`_db_write_lock`
    for the duration of the apply AND holds it across asyncio
    cancellation (see the helper's docstring for the cancel-vs-executor-
    thread rationale). The lock is the single FIFO serialisation point
    shared with every future external writer (the watcher, agent
    lifecycle, etc.). Acquiring per-branch rather than once around the
    whole function gives the lock 3 release points per tick — a future
    external waiter never has to wait longer than one apply, even when
    all three queues happen to fire on the same tick. The pulls
    themselves (``get_nowait``) run outside the lock: they are
    synchronous and never block, and we only need the lock once we have
    actual work to apply. The coroutine factory passed to the helper is
    a ``lambda`` capturing the dequeued message, so the apply coroutine
    is only created after the lock has been acquired — a cancellation
    while waiting for the lock cannot leave an unawaited coroutine
    (or, equivalently, a silently dropped dequeued message).

    Never raises an ordinary exception: a message is removed from its queue
    before processing, and every per-message failure (deserialization, apply,
    finalization) is caught and logged. So one bad message can neither crash a
    steady-state DB writer tick nor abort the shutdown drain — which would
    strand the messages queued behind it.
    """
    any_processed = False

    # ---- Compute side (mp.Queue, shared by every provider) ----
    try:
        raw = _subprocess_queue.get_nowait()
    except queue.Empty:
        pass
    except Exception as exc:
        # The mp.Queue is never closed, so get_nowait() should only ever raise
        # Empty; guard anyway so an unexpected failure cannot abort the drain.
        logger.error(f"Compute result queue read failed: {exc}", exc_info=True)
    else:
        any_processed = True
        try:
            msg = orjson.loads(raw)
        except Exception:
            logger.error(f"Failed to deserialize compute message: {raw!r:.500}")
        else:
            with provider_log_context(_provider_from_compute_message(msg)):
                try:
                    await run_under_db_write_lock(lambda: _process_compute_message(msg))
                except Exception as exc:
                    logger.error(f"Error processing compute message: {exc}", exc_info=True)

    # ---- Thread queue (initial-sync, shared by every provider) ----
    try:
        payload = _thread_queue.get_nowait()
    except queue.Empty:
        pass
    else:
        any_processed = True
        with provider_log_context(getattr(payload, "provider", None)):
            try:
                await run_under_db_write_lock(lambda: _process_thread_message(payload))
            except Exception as exc:
                logger.error(f"Error processing thread message: {exc}", exc_info=True)

    # ---- DB writer-side jobs (intra-process) ----
    try:
        job = _async_queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    else:
        any_processed = True
        with provider_log_context(getattr(job, "provider", None)):
            await run_under_db_write_lock(lambda: _dispatch_async_job(job))

    return any_processed


def _provider_from_compute_message(msg: dict) -> "Provider | None":
    """Extract the ``Provider`` from a compute-side raw JSON message, if any.

    The subprocess tags every message with ``provider`` (a ``Provider.value``
    string) but ``orjson.loads`` returns it as a plain str — translate
    back to the enum for :func:`provider_log_context`. Unknown / missing
    values fall back to ``None`` (logged as ``"global"``); the dispatch
    in :func:`_process_compute_message` will surface its own error.
    """
    raw_provider = msg.get("provider")
    if raw_provider is None:
        return None
    try:
        return Provider(raw_provider)
    except ValueError:
        return None


def _settle_unhandled_async_job(job, exc: Exception) -> None:
    """Settle a rejected async-queue job so its producer never hangs.

    Two completion shapes coexist on the async queue: the periodic-task
    jobs (and any provider-specific job following the same convention)
    carry a ``future`` settled with a result or exception, while
    :class:`_AbandonComputeRunJob` carries a ``done`` Future that the
    producer (shutdown) only awaits as a "DB writer reached this point"
    signal — that one resolves with ``None`` even on failure so shutdown
    can never hang.
    """
    future = getattr(job, "future", None)
    if future is not None and not future.done():
        future.set_exception(exc)
        return
    done = getattr(job, "done", None)
    if done is not None and not done.done():
        done.set_result(None)


async def _dispatch_async_job(job) -> None:
    """Apply one DB writer job and settle its caller-visible Future.

    Two job families are dispatched here:

    - **Generic, cross-provider jobs** owned by this module:
      :class:`_AbandonComputeRunJob` (a finalization request from a
      provider's ``shutdown()``; the producer awaits ``job.done`` and only
      cares that the DB writer reached this point, so we always resolve it
      with ``None`` — a hang would block shutdown) and the four periodic-
      task jobs (:class:`_ApplyDesiredCommandsJob`,
      :class:`_CreateUsageSnapshotJob`, :class:`_RetireSessionsJob`,
      :class:`_PersistProviderPricesJob`) carry the actual apply result;
      we settle their ``job.future`` with the result on success, or with
      the raised exception so the producer can log / surface it instead
      of silently dropping the failure.
    - **Provider-specific jobs** owned by a provider's helper module
      (e.g. cron-restart preparation, which is Claude Code-only). When no
      generic ``isinstance`` matches, we fall back to the helpers registry
      and ask each provider in turn via
      :meth:`BaseProviderHelpers.try_handle_async_job`; the first one
      that returns ``True`` wins. Lets a provider keep its job type +
      apply handler co-located in its own subpackage without polluting
      this module's vocabulary.

    Every per-job apply is wrapped here so one bad message can neither crash
    the DB writer tick nor strand the messages queued behind it. Two
    safety nets specifically protect the producer from hanging on
    ``job.future``:

    - **Convention check up front**: every async job MUST declare a
      ``provider`` field — either a :class:`Provider` value (per-provider
      jobs) or ``None`` (cross-provider / system jobs such as
      :class:`_MarkSessionsIndexedJob`, which sweeps sessions of every
      provider in one shot). The field is read by :func:`_drain_one` to
      bind :func:`provider_log_context` around the apply, so log records
      emitted from inside the apply carry the right provider tag (``None``
      falls back to the ``"global"`` tag). A job missing the field outright,
      or carrying a non-Provider non-None value, is rejected before dispatch
      and its future settled with a ``TypeError``.
    - **Unmatched job at the end**: if no generic ``isinstance`` matched
      AND no provider helper claimed the job, we settle the future with
      a ``RuntimeError`` rather than ``return``-ing silently.
    """
    if not hasattr(job, "provider"):
        exc = TypeError(
            f"Async-queue job {type(job).__name__} is missing a "
            f"`provider` field; every async-queue job must declare one "
            f"(a Provider value, or None for cross-provider jobs) per "
            f"the BaseProviderHelpers.try_handle_async_job contract"
        )
        logger.error(str(exc))
        _settle_unhandled_async_job(job, exc)
        return
    provider = job.provider
    if provider is not None and not isinstance(provider, Provider):
        exc = TypeError(
            f"Async-queue job {type(job).__name__} has an invalid "
            f"`provider` value {provider!r}; expected a Provider enum "
            f"member or None for cross-provider jobs"
        )
        logger.error(str(exc))
        _settle_unhandled_async_job(job, exc)
        return

    if isinstance(job, _AbandonComputeRunJob):
        try:
            await _finalize_abandoned_run(job.run_id, job.provider)
        except Exception as exc:
            logger.error(
                f"Error finalizing abandoned compute run {job.run_id}: {exc}",
                exc_info=True,
            )
        finally:
            if not job.done.done():
                job.done.set_result(None)
        return

    if isinstance(job, _ApplyDesiredCommandsJob):
        await _settle_async_job(job, _apply_desired_commands_job, "commands sync")
        return

    if isinstance(job, _CreateUsageSnapshotJob):
        await _settle_async_job(job, _apply_create_usage_snapshot_job, "usage sync")
        return

    if isinstance(job, _RetireSessionsJob):
        await _settle_async_job(job, _apply_retire_sessions_job, "model retirement")
        return

    if isinstance(job, _PersistProviderPricesJob):
        await _settle_async_job(job, _apply_persist_provider_prices_job, "price sync")
        return

    if isinstance(job, _MarkSessionsIndexedJob):
        await _settle_async_job(job, _apply_mark_sessions_indexed_job, "search-index mark")
        return

    # Fallback: ask every provider's helper. Lazy import to avoid a cycle —
    # ``twicc.providers.helpers`` instantiates each provider's helpers,
    # which transitively imports Django models / this module's payloads.
    from twicc.providers.helpers import get_provider_helpers_registry

    for helpers in get_provider_helpers_registry().values():
        try:
            handled = await helpers.try_handle_async_job(job, _settle_async_job)
        except Exception as exc:
            logger.error(
                "Provider helper %s.try_handle_async_job raised on job %s: %s",
                type(helpers).__name__, type(job).__name__, exc, exc_info=True,
            )
            handled = False
        if handled:
            return

    # No handler claimed the job — settle the future so the producer's
    # ``await submit_async_job(...)`` doesn't hang forever. The producer
    # sees a RuntimeError, which is the right shape: this is a
    # programming error (forgot to register a handler, helper override
    # has a bug), not a runtime condition the producer can retry.
    exc = RuntimeError(
        f"No DB writer handler matched async-queue job "
        f"{type(job).__name__} — neither the generic dispatch nor any "
        f"provider helper claimed it"
    )
    logger.error(f"{exc} => {job!r:.300}")
    _settle_unhandled_async_job(job, exc)


async def _settle_async_job(job, apply_fn, label: str) -> None:
    """Run a periodic-task job's sync apply, then settle its Future.

    ``apply_fn`` is a synchronous function (it runs in ``transaction.atomic``
    on a worker thread via ``sync_to_async``) that takes the job and returns
    the value the caller awaits. Any exception is logged and forwarded to the
    Future as an exception result, so the producer sees a real failure rather
    than a stranded ``await``.

    The ``provider`` attribute carried by every job (either a
    :class:`twicc.core.enums.Provider` for per-provider jobs or ``None``
    for cross-provider / system jobs like :class:`_MarkSessionsIndexedJob`)
    is not read here — :func:`_drain_one` already wraps the dispatch in
    :func:`provider_log_context` so log records emitted from the apply
    carry the right tag. This function's own logging uses ``label`` so the
    failure line identifies which periodic task failed without needing the
    provider value.
    """
    try:
        result = await sync_to_async(apply_fn)(job)
    except Exception as exc:
        logger.error(
            f"Error applying {label} job: {exc}",
            exc_info=True,
        )
        if not job.future.done():
            job.future.set_exception(exc)
        return
    if not job.future.done():
        job.future.set_result(result)


async def _db_writer_loop() -> None:
    """Drain the producer queues and DB writer jobs until the app shuts down.

    Permanent: runs from start_db_writer() to stop_db_writer().
    Resilient: an unexpected exception in one tick is logged and the loop
    continues — a single bad message must never take the writer down.

    On stop, before exiting, every queue is fully drained.
    stop_db_writer() runs only after every producer has shut down, so
    the queues can no longer grow — draining them guarantees no boot-time
    write still queued at shutdown is silently abandoned.
    """
    assert _db_writer_stop_event is not None
    assert _thread_queue is not None
    assert _subprocess_queue is not None

    logger.info("DB writer loop running")
    while not _db_writer_stop_event.is_set():
        try:
            any_processed = await _drain_one()
            # Yield: tightly when busy, back off when idle.
            await asyncio.sleep(0 if any_processed else 0.05)
        except Exception as exc:
            logger.error(f"DB writer tick crashed: {exc}", exc_info=True)
            await asyncio.sleep(0.05)

    # Final drain: producers are all stopped by the time stop is signalled, so
    # the queues only shrink — apply everything still queued before exiting.
    # On a crashed tick, log and continue (mirroring the steady-state loop):
    # a bad message is removed from its queue before processing, so the next
    # pass still makes progress. A break here would strand every message
    # queued behind the bad one. _drain_one() is written not to raise, so the
    # except is a last-resort guard.
    drained_passes = 0
    while True:
        try:
            if not await _drain_one():
                break
        except Exception as exc:
            logger.error(f"DB writer shutdown-drain tick crashed: {exc}", exc_info=True)
            continue
        drained_passes += 1
    if drained_passes:
        logger.info(
            "DB writer drained %d queued message pass(es) at shutdown",
            drained_passes,
        )
    logger.info("DB writer loop exited")


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

    # Every message is tagged with the run_id arm_compute_completion() minted;
    # the DB writer routes state/completion by run_id so a stale message from a
    # cancelled run can never touch a hot-restarted one.
    run_id = msg.get("run_id")

    if msg_type == "done":
        await _finalize_compute_run(run_id, provider)
        return

    if msg_type == "error":
        logger.error(f"Compute error for {msg.get('session_id')}: {msg.get('error')}")
        state = _compute_states.get(run_id)
        if state is not None:
            state.failed_count += 1
        return

    if msg_type != "session_complete":
        logger.error(f"Unexpected compute message type: {msg_type} => {msg!r:.300}")
        return

    # session_complete — the heavy path.
    state = _compute_states.get(run_id)
    if state is None or state.abandoned:
        # No live state for this run_id: either the run was never tracked / its
        # state already dropped, or it was abandoned at provider shutdown
        # (abandon_compute_run flags it; the DB writer finalizes it via an
        # _AbandonComputeRunJob). Ignore the message — do NOT apply it. The
        # metadata was computed against a possibly-outdated view of the session
        # and could clobber fresher data written since (by a hot-restarted run,
        # or by the watcher). The session's compute_version was never advanced
        # by this skipped message, so a live run (or the watcher) recomputes it.
        logger.info(
            "Ignoring session_complete for an untracked or abandoned compute "
            "run (run_id=%s, session_id=%s) — stale run",
            run_id, msg.get("session_id"),
        )
        return

    try:
        from twicc.providers.compute_base import BaseSessionCompute
        await sync_to_async(BaseSessionCompute.apply_session_complete)(msg)
        await _handle_compute_done(msg["session_id"])
    except Exception as e:
        logger.error(f"Error applying session_complete: {e}", exc_info=True)
        state.failed_count += 1
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


async def _finalize_compute_run(run_id: int | None, provider: Provider) -> None:
    """Drain-time finalisation when a compute run's worker is done.

    Flushes the run's pending broadcasts + activities, resolves its completion
    Future with the run's failed-session count, and drops its state so the
    next run starts clean. A 'done' for an untracked ``run_id`` (a stale
    message from a cancelled run) is ignored — it must not touch a live run.
    """
    state = _compute_states.pop(run_id, None)
    future = _compute_done_events.pop(run_id, None)
    if state is None and future is None:
        logger.info(
            "compute 'done' for an untracked run (run_id=%s) — "
            "ignoring (stale message from a cancelled run)",
            run_id,
        )
        return

    logger.info(f"DB writer: compute 'done' (run_id={run_id})")
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

    # Resolve the completion Future with the run's failed-session count; the
    # compute orchestrator (start_background_compute_task) logs the summary.
    if future is not None and not future.done():
        future.set_result(state.failed_count if state is not None else 0)


async def _finalize_abandoned_run(run_id: int, provider: Provider) -> None:
    """DB writer-side finalization of a compute run abandoned at shutdown.

    Runs inside the DB writer task (dispatched via ``_async_queue`` by
    :func:`abandon_compute_run`), so the activity-recalculation flush is
    serialized with every other DB writer write — the single-writer guarantee
    holds. The DB writer handles one message at a time, so any in-flight
    ``session_complete`` for the run, and its post-apply bookkeeping, has
    already finished by the time this runs: ``state.pending_activity_days``
    and ``state.pending_project_ids`` are final.

    Flushes the run's batched project broadcasts and activity recalculations
    for the sessions applied before the abandon — without this, those
    already-applied sessions would keep their ``compute_version`` current (so
    the next start does not recompute them) yet leave their ``PeriodicActivity``
    rows stale — then drops the run state. Unlike :func:`_finalize_compute_run`
    it does not resolve the completion Future: the orchestrator that armed the
    run is shutting the provider down and cancels the compute task that
    awaited it.
    """
    state = _compute_states.pop(run_id, None)
    _compute_done_events.pop(run_id, None)
    if state is None:
        # Already finalized by _finalize_compute_run — the worker emitted
        # 'done' between abandon_compute_run flagging the run and this job
        # running, so the flush already happened. Nothing left to do.
        return

    logger.info(
        "Finalizing abandoned compute run_id=%d "
        "(%d session(s) applied, %d failed) — remaining queued results "
        "are ignored",
        run_id, state.completed_count, state.failed_count,
    )
    for pid in state.pending_project_ids:
        try:
            await _broadcast_project_updated(pid)
        except Exception as e:
            logger.error(f"Error in abandoned-run project broadcast for {pid}: {e}")
    if state.pending_activity_days:
        try:
            await _flush_pending_activities(provider, state.pending_activity_days)
        except Exception as e:
            logger.error(f"Error in abandoned-run activity flush: {e}", exc_info=True)


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


async def _broadcast_project_added(project) -> None:
    """Broadcast project_added for a newly-created project.

    Called by the initial-sync DB writer *after* the create-session
    transaction has committed — never from inside it.
    """
    from twicc.core.serializers import serialize_project

    try:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "project_added",
                "project": serialize_project(project),
            }},
        )
    except Exception as e:
        logger.error(f"Error broadcasting project_added for {project.id}: {e}")


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

    # One atomic batch for the whole flush, like every other DB writer write.
    with transaction.atomic():
        for project_id, days in pending_activity_days.items():
            PeriodicActivity.recalculate_for_days(project_id, days, provider=provider, do_global=False)
        days = set.union(*pending_activity_days.values())
        PeriodicActivity.recalculate_for_days(None, days, provider=provider, do_global=True)


# =============================================================================
# Initial-sync message handling
# =============================================================================


async def _process_thread_message(msg) -> None:
    """Apply one message drained from the shared thread queue."""
    if isinstance(msg, InitialSyncDoneMarker):
        # Drained after every real payload of the run (FIFO). Resolve the
        # marker's Future with the run's failure count so the producer learns
        # the run was not clean. Handled outside the try below so the Future
        # is always resolved.
        failures = _initial_sync_failures.pop(msg.provider, 0)
        orphan_skips = _initial_sync_orphan_skips.pop(msg.provider, 0)
        logger.info("DB writer: initial-sync 'done'")
        if orphan_skips:
            logger.warning(
                "Initial sync: %d subagent(s) skipped — parent "
                "row absent (orphan, or an upstream payload failed)",
                orphan_skips,
            )
        if not msg.done_future.done():
            msg.done_future.set_result(failures)
        return

    try:
        if isinstance(msg, CreateSessionPayload):
            project, created, adopted = await sync_to_async(_apply_create_session_payload)(msg)
            if project is not None:
                # Post-commit side effects, kept out of transaction.atomic so
                # a project is never announced before it commits. Workspace
                # auto-add runs only when the project was just created or just
                # adopted a directory — not for every session of an existing
                # project (its workspace membership cannot change per-session).
                if created:
                    await _broadcast_project_added(project)
                if (created or adopted) and project.directory:
                    await auto_add_project_to_workspaces(project.id, project.directory)
            else:
                # _apply_create_session_payload skipped an orphan subagent
                # (parent row absent). A deliberate skip, not an apply error —
                # counted separately, but still surfaced in the run summary.
                _initial_sync_orphan_skips[msg.provider] += 1
        elif isinstance(msg, UpdateSessionPayload):
            await sync_to_async(_apply_update_session_payload)(msg)
        elif isinstance(msg, MarkSessionsStalePayload):
            await sync_to_async(_apply_mark_sessions_stale_payload)(msg)
        elif isinstance(msg, UpdateProjectMetadataPayload):
            await sync_to_async(_apply_update_project_metadata_payload)(msg)
        elif isinstance(msg, ResolveProjectGitRootsPayload):
            await sync_to_async(_apply_resolve_git_roots_payload)(msg)
        else:
            logger.error(
                f"Unexpected initial-sync message type: {type(msg).__name__} => {msg!r:.300}"
            )
    except Exception as e:
        logger.error(
            f"Error processing initial-sync message {type(msg).__name__}: {e}",
            exc_info=True,
        )
        provider = getattr(msg, "provider", None)
        if provider is not None:
            _initial_sync_failures[provider] += 1


def _apply_create_session_payload(payload: CreateSessionPayload) -> tuple[object, bool, bool]:
    """Persist a new session (and project if missing) plus its items, atomically.

    Returns ``(project, project_was_created, adopted_directory)`` so the async
    caller can run the post-commit side effects — the ``project_added``
    broadcast and workspace auto-add — *outside* this transaction.
    ``register_project_db_only`` is the DB-only half of project registration
    precisely so the broadcast never fires from inside ``transaction.atomic``.

    Returns ``(None, False, False)`` when the payload is a subagent whose
    parent row is absent — an upstream CreateSessionPayload was rejected, or
    the parent is an orphan. The session is skipped cleanly with one warning
    rather than cascading into an opaque IntegrityError on the parent_session
    FK.
    """
    from twicc.core.models import Session, SessionItem
    from twicc.projects import register_project_db_only

    parent_id = payload.session.parent_session_id
    if parent_id is not None and not Session.objects.filter(id=parent_id).exists():
        logger.warning(
            "Skipping session %s: parent %s does not exist "
            "(upstream payload rejected, or orphan subagent)",
            payload.session.id, parent_id,
        )
        return None, False, False

    with transaction.atomic():
        project, created, adopted = register_project_db_only(
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
    return project, created, adopted


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


def _compute_project_mtime(project_id: str) -> float:
    """Max ``mtime`` over a project's visible, non-stale SESSION rows (0 if none).

    Shared by ``recalc_mtime`` in :func:`_apply_update_project_metadata_payload`
    and the per-project refresh in :func:`_apply_mark_sessions_stale_payload`.
    Same filter as the ``mtime`` aggregate in ``update_project_metadata()``:
    the visible-session filter plus ``stale=False`` so a session whose JSONL
    is gone from disk does not keep the project's mtime up.
    """
    from django.db.models import Max

    from twicc.core.models import Session, SessionType

    return Session.objects.filter(
        project_id=project_id,
        type=SessionType.SESSION,
        created_at__isnull=False,
        user_message_count__gt=0,
        stale=False,
    ).aggregate(value=Max("mtime"))["value"] or 0


def _apply_mark_sessions_stale_payload(payload: MarkSessionsStalePayload) -> None:
    """Mark a batch of sessions stale, then refresh their projects' mtime.

    Marking sessions stale removes them from the project-mtime aggregate
    (:func:`_compute_project_mtime` excludes ``stale=True``), so a session
    whose file is gone from disk no longer keeps its project's ``mtime`` — and
    its sort position — high. Self-contained on purpose: every producer that
    marks sessions stale gets the refresh without enqueueing a follow-up
    ``UpdateProjectMetadataPayload`` of its own.
    """
    from twicc.core.models import Project, Session

    if not payload.session_ids:
        return
    with transaction.atomic():
        affected_project_ids = set(
            Session.objects.filter(id__in=payload.session_ids)
            .values_list("project_id", flat=True)
        )
        Session.objects.filter(id__in=payload.session_ids, stale=False).update(stale=True)
        for project_id in affected_project_ids:
            Project.objects.filter(id=project_id).update(
                mtime=_compute_project_mtime(project_id)
            )


def _apply_update_project_metadata_payload(payload: UpdateProjectMetadataPayload) -> None:
    """Apply end-of-sync metadata updates for one project, atomically."""
    from twicc.core.models import Project, Session, SessionType
    from twicc.projects import ensure_project_git_root, update_project_total_cost

    with transaction.atomic():
        if (payload.recalc_sessions_count
                or payload.recalc_mtime
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
                if payload.recalc_sessions_count:
                    # Recompute from the DB (not a producer-supplied count):
                    # sessions_count is cross-provider, so a value counted in
                    # the producer could clobber a fresher one another
                    # provider's compute wrote. Same filter as
                    # update_project_metadata().
                    project.sessions_count = Session.objects.filter(
                        project_id=payload.project_id,
                        type=SessionType.SESSION,
                        created_at__isnull=False,
                        user_message_count__gt=0,
                    ).count()
                    update_fields.append("sessions_count")
                if payload.recalc_mtime:
                    # Recompute from the DB now that every session payload
                    # this provider pushed for the project has landed (FIFO).
                    project.mtime = _compute_project_mtime(payload.project_id)
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


def _apply_resolve_git_roots_payload(payload: ResolveProjectGitRootsPayload) -> None:
    """Resolve git_root for every non-stale project that has a directory.

    Run by the DB writer when it drains a ResolveProjectGitRootsPayload —
    after every prior FIFO project payload of the same sync run has
    committed — so the ``stale=False`` filter reflects this run's fresh
    state (a project being un-staled earlier in the same run is no longer
    wrongly excluded). ``ensure_project_git_root`` is idempotent and writes
    only when the resolved root actually changes.
    """
    from twicc.core.models import Project
    from twicc.projects import ensure_project_git_root

    # One atomic batch for the whole payload, like every other DB writer write.
    with transaction.atomic():
        for project in Project.objects.filter(directory__isnull=False, stale=False):
            ensure_project_git_root(project.id, project.directory)


# =============================================================================
# Periodic-task job handlers (commands, usage, model retirement, pricing)
# =============================================================================
#
# Each handler runs synchronously on a worker thread (via ``sync_to_async`` in
# :func:`_settle_async_job`) inside its own ``transaction.atomic``, like
# every other DB writer write. The producer side prepared everything that does
# not touch DB (HTTP fetches, filesystem scans, parsing); only the actual
# diff/write happens here.


def _apply_desired_commands_job(job: _ApplyDesiredCommandsJob) -> dict[str, int]:
    """Reconcile ``Command`` rows for one ``(provider, activation_char)`` scope.

    Delegates to the existing shared helper :func:`apply_desired_commands`
    so the diff/apply logic stays in one place — wrapped here in
    ``transaction.atomic`` so the SELECT current + delete/create/update all
    commit (or roll back) together, and so the helper is never invoked from a
    different writer.
    """
    from twicc.providers.commands_sync import apply_desired_commands

    with transaction.atomic():
        return apply_desired_commands(
            provider=job.provider.value,
            activation_char=job.activation_char,
            desired=job.desired,
        )


def _apply_create_usage_snapshot_job(job: _CreateUsageSnapshotJob) -> object:
    """Insert one ``UsageSnapshot`` row from the producer-prepared ``fields``.

    The producer (each provider's ``usage_task``) parsed the API response
    into ``fields`` before submitting, so this is a single ``create()`` — no
    upstream parsing happens in the DB writer thread.
    """
    from twicc.core.models import UsageSnapshot

    with transaction.atomic():
        return UsageSnapshot.objects.create(**job.fields)


def _apply_retire_sessions_job(job: _RetireSessionsJob) -> int:
    """Apply per-session field updates for retirement-driven upgrades.

    ``updates`` maps each session id to the field/value dict the producer
    wants applied (``selected_model``, possibly ``effort``). Sessions that no
    longer exist (e.g. the row was deleted between the producer's iteration
    and the DB writer's apply) contribute 0 to the count — a transient miss,
    not an error. Returns the number of rows actually updated.
    """
    from twicc.core.models import Session

    if not job.updates:
        return 0
    count = 0
    with transaction.atomic():
        for session_id, fields in job.updates.items():
            count += Session.objects.filter(id=session_id).update(**fields)
    return count


def _apply_persist_provider_prices_job(job: _PersistProviderPricesJob) -> dict[str, int]:
    """Persist OpenRouter prices for one provider.

    Delegates to the existing :func:`persist_provider_prices` helper so the
    SELECT-latest / INSERT-on-change logic stays in one place — wrapped here
    in ``transaction.atomic`` so the per-row SELECT and INSERT pairs commit
    (or roll back) together, and so the helper is never invoked from a
    different writer.
    """
    from twicc.pricing import persist_provider_prices

    with transaction.atomic():
        return persist_provider_prices(job.provider, job.prices)


def _apply_mark_sessions_indexed_job(job: _MarkSessionsIndexedJob) -> int:
    """Bump ``search_version`` on a batch of sessions that just hit Tantivy.

    One ``UPDATE Session SET search_version = ? WHERE id IN (...)`` per job,
    wrapped in ``transaction.atomic`` for consistency with every other apply
    on this writer. Sessions that no longer exist (e.g. the row was deleted
    between the indexer's ``_index_session`` and the DB writer's apply)
    contribute 0 to the count — a transient miss, not an error. Returns the
    number of rows actually updated so the producer can sanity-check the
    batch volumetry in its logs if needed.
    """
    from django.conf import settings

    from twicc.core.models import Session

    if not job.session_ids:
        return 0
    with transaction.atomic():
        return Session.objects.filter(id__in=job.session_ids).update(
            search_version=settings.CURRENT_SEARCH_VERSION
        )
