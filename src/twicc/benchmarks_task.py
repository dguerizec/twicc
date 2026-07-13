"""Cross-provider model-benchmark sync orchestration.

DeepSWE publishes one static leaderboard JSON covering every model, so we
fetch it **once** per cycle and upsert the rows for the models we support
(see :mod:`twicc.benchmarks`). The lifecycle mirrors the OpenRouter price
sync (:mod:`twicc.pricing_task`): the CLI (:mod:`twicc.cli.run`) creates
:func:`start_benchmark_sync_task` as a fire-and-forget periodic loop,
cancelled via the shared shutdown event when the server stops.

The DB-write step (``persist_benchmarks``) is routed through the DB writer
via :class:`_PersistModelBenchmarksJob`, so a benchmark sync can never race
the initial price sync drain or the per-provider compute writes on the SQLite
write lock.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.benchmarks import extract_benchmarks, fetch_deepswe_leaderboard

logger = logging.getLogger(__name__)


# Single global cadence: one fetch of the static leaderboard covers every
# provider, matching the price sync's single-URL daily interval.
BENCHMARK_SYNC_INTERVAL = 24 * 60 * 60


def _format_stats(stats: dict[str, int]) -> str:
    """Render the persist stats for log output."""
    return f"{stats.get('created', 0)} created, {stats.get('updated', 0)} updated"


def _fetch_and_extract_blocking() -> list[dict]:
    """Fetch the DeepSWE leaderboard and map it to our supported models.

    Pure I/O + parsing + registry lookup — runs in a worker thread (no event
    loop, no DB). Returns rows ready to feed :class:`_PersistModelBenchmarksJob`
    for the DB writer-side persist.
    """
    rows = fetch_deepswe_leaderboard()
    return extract_benchmarks(rows)


async def _persist_via_db_writer(benchmarks: list[dict]) -> dict[str, int]:
    """Submit a :class:`_PersistModelBenchmarksJob` and await its stats.

    The fetch + extract step that built ``benchmarks`` already ran on a worker
    thread; here we only do the DB-writer-routed upsert so the benchmark write
    can never race another writer on the SQLite write lock.
    """
    from twicc.providers.db_writer import _PersistModelBenchmarksJob, submit_async_job

    future = asyncio.get_running_loop().create_future()
    return await submit_async_job(_PersistModelBenchmarksJob(
        benchmarks=benchmarks,
        future=future,
    ))


async def _run_sync_cycle() -> None:
    """Run one fetch + extract + DB-writer-routed persist, logging the outcome.

    Resilient: a transient fetch or persist failure is logged and swallowed so
    the periodic loop stays alive for the next cycle.
    """
    try:
        benchmarks = await asyncio.to_thread(_fetch_and_extract_blocking)
    except Exception as e:  # noqa: BLE001 — keep loop alive across transient errors
        logger.error("Model benchmark sync fetch failed: %s", e, exc_info=True)
        return

    try:
        stats = await _persist_via_db_writer(benchmarks)
    except Exception as e:  # noqa: BLE001 — keep loop alive across transient errors
        logger.error("Model benchmark sync persist failed: %s", e, exc_info=True)
        return

    logger.info("Model benchmark sync completed: %s", _format_stats(stats))


async def start_benchmark_sync_task(stop_event: asyncio.Event) -> None:
    """Periodic model-benchmark sync loop.

    Runs one cycle immediately so the table is populated shortly after boot,
    then re-runs the fetch + DB-writer-routed persist every
    :data:`BENCHMARK_SYNC_INTERVAL` seconds until ``stop_event`` is set. Unlike
    the price sync there is no separate awaited initial sync: benchmarks have no
    boot-time consumer to gate on, and this loop is created as a fire-and-forget
    task by ``run_server``, so the first cycle populates the table without
    blocking startup.
    """
    logger.info("Model benchmark sync task started")

    try:
        await _run_sync_cycle()

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=BENCHMARK_SYNC_INTERVAL)
            except asyncio.TimeoutError:
                # Timeout means it's time to sync again
                pass
            else:
                # stop_event fired — exit before doing another sync
                break

            await _run_sync_cycle()
    finally:
        logger.info("Model benchmark sync task stopped")
