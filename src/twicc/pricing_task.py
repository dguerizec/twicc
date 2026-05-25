"""Cross-provider price sync orchestration.

OpenRouter exposes one ``/api/v1/models`` endpoint that lists every
provider's prices in a single response. We therefore fetch it **once**
per cycle and dispatch the resulting rows to each provider that has
declared its :attr:`BaseProviderHelpers.OPENROUTER_MODEL_PREFIX` — no
per-provider HTTP fan-out, no per-provider scheduler.

The CLI (:mod:`twicc.cli.run`) drives the lifecycle the same way it
drives :mod:`twicc.version_check_task`:

- :func:`sync_all_providers` runs the initial sync at startup, after
  the unified DB writer is up so the actual price inserts flow through
  the single-writer path.
- :func:`start_price_sync_task` is the long-running periodic loop,
  cancelled via the shared shutdown event when the server stops.

The DB-write step (``persist_provider_prices``) is routed through the
unified consumer via :class:`_PersistProviderPricesJob`, so a price sync
can never race the initial-sync drain or the per-provider compute writes
on the SQLite write lock — and a hot-toggle that triggers a fresh boot-
time sync at any moment stays safe.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.pricing import extract_provider_prices, fetch_openrouter_models

logger = logging.getLogger(__name__)


# Single global cadence: one fetch covers every provider, so a per-provider
# interval would just split the same HTTP call into N calls of the same URL.
PRICE_SYNC_INTERVAL = 24 * 60 * 60


def _format_stats(stats: dict[str, int]) -> str:
    """Render the persist stats for log output."""
    return f"{stats.get('created', 0)} created, {stats.get('unchanged', 0)} unchanged"


def _fetch_and_extract_all_blocking() -> dict:
    """Fetch the OpenRouter catalogue and extract per-provider price rows.

    Pure I/O + parsing — runs in a worker thread (no event loop, no DB).
    Returns a mapping ``{Provider: list[dict]}`` whose values are ready to
    feed :class:`_PersistProviderPricesJob` for the consumer-side persist.
    """
    from twicc.providers.helpers import get_provider_helpers_registry

    models = fetch_openrouter_models()
    extracted: dict = {}
    for provider, helpers in get_provider_helpers_registry().items():
        if not helpers.OPENROUTER_MODEL_PREFIX:
            continue
        extracted[provider] = extract_provider_prices(models, provider)
    return extracted


async def _persist_all_via_consumer(extracted: dict) -> dict[str, dict[str, int]]:
    """Submit one :class:`_PersistProviderPricesJob` per provider and await each.

    The fetch + extract step that built ``extracted`` already ran on a worker
    thread; here we only do the consumer-routed persists. A per-provider
    failure is logged but does not abort the other providers — a single bad
    response should never strand the rest of the catalogue.

    Returns ``{provider_value: stats}`` for every provider that produced a
    completed apply (skipping any whose job raised).
    """
    from twicc.providers.db_writer import _PersistProviderPricesJob, submit_consumer_job

    results: dict[str, dict[str, int]] = {}
    for provider, prices in extracted.items():
        future = asyncio.get_running_loop().create_future()
        try:
            stats = await submit_consumer_job(_PersistProviderPricesJob(
                provider=provider,
                prices=prices,
                future=future,
            ))
        except Exception as e:  # noqa: BLE001 — one provider's failure must not strand the rest
            logger.error(
                "Price sync persist failed for provider=%s: %s",
                provider.value, e, exc_info=True,
            )
            continue
        results[provider.value] = stats
    return results


async def sync_all_providers() -> dict[str, dict[str, int]]:
    """Run a one-shot price sync covering every OpenRouter-priced provider.

    Used at startup after the unified DB writer is up: a failure here is
    non-fatal — existing DB rows or each helper's
    :attr:`DEFAULT_FAMILY_PRICES` continue to serve as fallbacks.
    """
    logger.info("Running initial price sync (cross-provider)...")
    try:
        extracted = await asyncio.to_thread(_fetch_and_extract_all_blocking)
    except Exception as e:  # noqa: BLE001 — startup robustness; logged below
        logger.warning(
            "Initial price sync fetch failed: %s. "
            "Will use existing DB prices or default family prices.",
            e,
        )
        return {}

    if not extracted:
        logger.info("Initial price sync: no providers configured")
        return {}

    results = await _persist_all_via_consumer(extracted)

    for provider_value, stats in results.items():
        logger.info(
            "Initial price sync (%s) completed: %s",
            provider_value, _format_stats(stats),
        )
    return results


async def start_price_sync_task(stop_event: asyncio.Event) -> None:
    """Periodic cross-provider price sync loop.

    Re-runs the fetch + consumer-routed persist every
    :data:`PRICE_SYNC_INTERVAL` seconds until ``stop_event`` is set.
    Caller is expected to have already run :func:`sync_all_providers`
    once at startup (the loop sleeps before its first iteration to
    avoid a redundant fetch immediately after the initial sync).
    """
    logger.info("Price sync task started (cross-provider)")

    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=PRICE_SYNC_INTERVAL)
            except asyncio.TimeoutError:
                # Timeout means it's time to sync again
                pass
            else:
                # stop_event fired — exit before doing another sync
                break

            try:
                extracted = await asyncio.to_thread(_fetch_and_extract_all_blocking)
            except Exception as e:  # noqa: BLE001 — keep loop alive across transient errors
                logger.error("Price sync fetch failed: %s", e, exc_info=True)
                continue

            results = await _persist_all_via_consumer(extracted)

            for provider_value, stats in results.items():
                logger.info(
                    "Price sync (%s) completed: %s",
                    provider_value, _format_stats(stats),
                )
    finally:
        logger.info("Price sync task stopped")
