"""Cross-provider price sync orchestration.

OpenRouter exposes one ``/api/v1/models`` endpoint that lists every
provider's prices in a single response. We therefore fetch it **once**
per cycle and dispatch the resulting rows to each provider that has
declared its :attr:`BaseProviderHelpers.OPENROUTER_MODEL_PREFIX` — no
per-provider HTTP fan-out, no per-provider scheduler.

The CLI (:mod:`twicc.cli.run`) drives the lifecycle the same way it
drives :mod:`twicc.version_check_task`:

- :func:`sync_all_providers` runs the initial sync at startup, before
  the per-provider orchestrators boot (so they can rely on prices
  being in DB).
- :func:`start_price_sync_task` is the long-running periodic loop,
  cancelled via the shared shutdown event when the server stops.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.pricing import (
    extract_provider_prices,
    fetch_openrouter_models,
    persist_provider_prices,
)

logger = logging.getLogger(__name__)


# Single global cadence: one fetch covers every provider, so a per-provider
# interval would just split the same HTTP call into N calls of the same URL.
PRICE_SYNC_INTERVAL = 24 * 60 * 60


def _format_stats(stats: dict[str, int]) -> str:
    """Render the ``persist_provider_prices`` stats for log output."""
    return f"{stats.get('created', 0)} created, {stats.get('unchanged', 0)} unchanged"


def _sync_all_providers_blocking() -> dict[str, dict[str, int]]:
    """Run the full sync synchronously: 1 fetch + persist per provider.

    Lives separately from the async wrappers so it can be called inside
    :func:`asyncio.to_thread` without dragging an event loop across
    blocking HTTP and DB work. Returns a ``{provider_value: stats}`` map.
    """
    from twicc.providers.helpers import get_provider_helpers_registry

    models = fetch_openrouter_models()
    results: dict[str, dict[str, int]] = {}
    for provider, helpers in get_provider_helpers_registry().items():
        if not helpers.OPENROUTER_MODEL_PREFIX:
            continue
        prices = extract_provider_prices(models, provider)
        results[provider.value] = persist_provider_prices(provider, prices)
    return results


async def sync_all_providers() -> dict[str, dict[str, int]]:
    """Run a one-shot price sync covering every OpenRouter-priced provider.

    Used at startup before the per-provider orchestrators come up: a
    failure here is non-fatal — existing DB rows or each helper's
    :attr:`DEFAULT_FAMILY_PRICES` continue to serve as fallbacks.
    """
    logger.info("Running initial price sync (cross-provider)...")
    try:
        results = await asyncio.to_thread(_sync_all_providers_blocking)
    except Exception as e:  # noqa: BLE001 — startup robustness; logged below
        logger.warning(
            "Initial price sync failed: %s. "
            "Will use existing DB prices or default family prices.",
            e,
        )
        return {}

    if not results:
        logger.info("Initial price sync: no providers configured")
        return results

    for provider_value, stats in results.items():
        logger.info(
            "Initial price sync (%s) completed: %s",
            provider_value, _format_stats(stats),
        )
    return results


async def start_price_sync_task(stop_event: asyncio.Event) -> None:
    """Periodic cross-provider price sync loop.

    Re-runs :func:`sync_all_providers` every
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
                results = await asyncio.to_thread(_sync_all_providers_blocking)
            except Exception as e:  # noqa: BLE001 — keep loop alive across transient errors
                logger.error("Price sync failed: %s", e, exc_info=True)
                continue

            for provider_value, stats in results.items():
                logger.info(
                    "Price sync (%s) completed: %s",
                    provider_value, _format_stats(stats),
                )
    finally:
        logger.info("Price sync task stopped")
