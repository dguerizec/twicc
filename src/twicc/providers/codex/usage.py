"""
Usage quota fetching and storage for Codex.

Calls ChatGPT's ``/backend-api/wham/usage`` endpoint — the same one the
Codex CLI polls itself for ``/status``. The response carries 5-hour and
weekly quota windows under ``rate_limit.primary_window`` and
``rate_limit.secondary_window``; we map them onto the existing
:class:`UsageSnapshot` columns so the cross-provider sidebar / graph
dialog can consume Codex snapshots through exactly the same shape as
Anthropic's ``five_hour`` / ``seven_day``.

Credentials access lives in :mod:`.credentials`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import orjson

from twicc.core.models import UsageSnapshot

from .credentials import (
    get_credentials,
    refresh_token_via_codex_sdk,
)

logger = logging.getLogger(__name__)

# ChatGPT internal endpoint used by the Codex CLI's /status command.
USAGE_API_URL = "https://chatgpt.com/backend-api/wham/usage"

# Headers the endpoint expects. The OAuth bearer is the ChatGPT
# ``access_token`` from ``~/.codex/auth.json``; ``ChatGPT-Account-Id``
# is the matching ``account_id``. Origin/Referer/User-Agent mimic a
# browser context — the endpoint rejects bare requests without them.
USAGE_API_BASE_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com/",
    "User-Agent": "Mozilla/5.0",
}


def fetch_usage(*, refresh_token_if_needed: bool = True) -> dict | None:
    """Fetch raw usage data from ChatGPT's ``/backend-api/wham/usage``.

    On 401/403, attempts to refresh the token by running a throwaway
    SDK turn against the bundled binary via
    :func:`refresh_token_via_codex_sdk`, then retries the fetch once
    when the refresh actually moved ``last_refresh`` forward.

    Returns the raw JSON payload (the unmodified ChatGPT response) on
    success, ``None`` on failure.
    """
    creds = get_credentials()
    if creds is None:
        return None

    headers = {
        **USAGE_API_BASE_HEADERS,
        "Authorization": f"Bearer {creds.access_token}",
        "ChatGPT-Account-Id": creds.account_id,
    }

    try:
        response = httpx.get(USAGE_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403) and refresh_token_if_needed:
            logger.warning("Codex usage API returned %d, attempting token refresh", e.response.status_code)
            if refresh_token_via_codex_sdk(creds.last_refresh):
                return fetch_usage(refresh_token_if_needed=False)
            return None
        logger.warning("Codex usage API HTTP error: %s", e)
        return None
    except httpx.TimeoutException:
        logger.warning("Codex usage API request timed out")
        return None
    except Exception as e:
        logger.warning("Codex usage API request failed: %s", e)
        return None


def _extract_window_fields(window: dict | None, prefix: str) -> dict:
    """Translate a Codex ``rate_limit`` window into ``UsageSnapshot`` columns.

    Codex uses ``used_percent`` (number 0-100) and ``reset_at`` (epoch
    seconds) — Anthropic uses ``utilization`` (0-100) and ``resets_at``
    (ISO datetime). The only real conversion is the timestamp: epoch
    seconds → timezone-aware UTC ``datetime``.

    ``prefix`` is the destination column prefix (``"five_hour"`` for the
    primary 5-hour window, ``"seven_day"`` for the secondary weekly
    window). Returns ``{f"{prefix}_utilization": float|None,
    f"{prefix}_resets_at": datetime|None}``.
    """
    if window is None:
        return {
            f"{prefix}_utilization": None,
            f"{prefix}_resets_at": None,
        }

    utilization = window.get("used_percent")
    if not isinstance(utilization, (int, float)):
        utilization = None
    else:
        utilization = float(utilization)

    reset_at_epoch = window.get("reset_at")
    resets_at = None
    if isinstance(reset_at_epoch, (int, float)):
        try:
            resets_at = datetime.fromtimestamp(reset_at_epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            logger.warning("Failed to parse reset_at for %s: %r", prefix, reset_at_epoch)

    return {
        f"{prefix}_utilization": utilization,
        f"{prefix}_resets_at": resets_at,
    }


def _build_usage_snapshot_fields(raw: dict) -> dict:
    """Translate a raw Codex usage response into ``UsageSnapshot`` columns.

    Pure — no DB access. The raw payload is preserved verbatim in
    ``raw_response`` so we can surface anything Codex-specific (``credits``,
    ``plan_type``, ``email``, …) from the wire snapshot later without a
    backfill. Quota windows are mapped onto the cross-provider columns so the
    front-end consumes the same shape as Claude Code's snapshots.

    Codex has no equivalent of Anthropic's ``extra_usage`` block (the
    semantics of ``credits.balance`` are *remaining* credits, not *used*
    ones, and would mislead the existing UI), so the ``extra_usage_*``
    columns stay at their default zero/null values.

    The actual ``UsageSnapshot.objects.create`` happens on the unified
    consumer's single-writer path, via :class:`_CreateUsageSnapshotJob`.
    """
    from twicc.core.enums import Provider

    rate_limit = raw.get("rate_limit") or {}

    fields: dict = {
        "provider": Provider.CODEX.value,
        "fetched_at": datetime.now(timezone.utc),
        "raw_response": raw,
    }
    fields.update(_extract_window_fields(rate_limit.get("primary_window"), "five_hour"))
    fields.update(_extract_window_fields(rate_limit.get("secondary_window"), "seven_day"))

    return fields


def read_usage_from_file(file_path: str) -> dict | None:
    """Load a previously dumped Codex usage payload from disk.

    Same shape as :func:`fetch_usage` would return — i.e. the raw
    ChatGPT response — so the rest of the pipeline doesn't care which
    source produced it.
    """
    path = Path(file_path)
    if not path.is_file():
        logger.warning("Codex usage JSON file not found: %s", file_path)
        return None

    try:
        return orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as e:
        logger.warning("Codex usage JSON file is not valid JSON: %s — %s", file_path, e)
        return None
    except OSError as e:
        logger.warning("Failed to read Codex usage JSON file: %s — %s", file_path, e)
        return None


# Top-level keys a payload must contain to be accepted as a Codex usage
# response. Used both by ``CodexHelpers.validate_usage_file_payload``
# (for the read-mode validation) and as documentation of the contract.
USAGE_REQUIRED_KEYS = {"rate_limit"}


def dump_usage_to_file(raw: dict, file_path: str) -> None:
    """Persist the raw API response to ``file_path`` for offline replay."""
    try:
        Path(file_path).write_bytes(orjson.dumps(raw, option=orjson.OPT_INDENT_2))
    except OSError as e:
        logger.warning("Failed to dump Codex usage to file: %s — %s", file_path, e)


def _fetch_usage_raw_blocking() -> dict | None:
    """Blocking half of :func:`fetch_and_save_usage` — pure I/O, no DB.

    Reads the synced settings and either loads a previously dumped payload
    from disk (read mode) or hits ChatGPT's ``wham/usage`` endpoint. On a
    successful API fetch, optionally dumps the raw response to disk. Returns
    the raw payload, or ``None`` when nothing usable was fetched.
    """
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    read_enabled = settings.get("codexUsageReadFileEnabled", False)
    read_path = settings.get("codexUsageReadFilePath", "")
    dump_enabled = settings.get("codexUsageDumpFileEnabled", False)
    dump_path = settings.get("codexUsageDumpFilePath", "")

    if read_enabled and read_path:
        return read_usage_from_file(read_path)

    raw = fetch_usage()
    if raw is not None and dump_enabled and dump_path:
        dump_usage_to_file(raw, dump_path)
    return raw


async def fetch_and_save_usage() -> UsageSnapshot | None:
    """Fetch a Codex usage payload (API or replay file) and persist via the consumer.

    Mirrors the Claude Code orchestration: when the user has enabled read
    mode in synced settings, load from the configured path instead of hitting
    the API; when dump mode is on, persist the fresh API response to the
    configured path before saving the snapshot. The two modes are mutually
    exclusive — read mode wins and dump is skipped.

    The HTTP / file read happens on a worker thread; the parsed-fields dict
    is then routed through the unified DB writer via
    :class:`_CreateUsageSnapshotJob`, so the ``UsageSnapshot.objects.create``
    never races the consumer on the SQLite write lock.
    """
    from twicc.core.enums import Provider
    from twicc.providers.db_writer import _CreateUsageSnapshotJob, submit_consumer_job

    raw = await asyncio.to_thread(_fetch_usage_raw_blocking)
    if raw is None:
        return None

    try:
        fields = _build_usage_snapshot_fields(raw)
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to parse Codex usage snapshot fields: %s", e, exc_info=True)
        return None

    future = asyncio.get_running_loop().create_future()
    try:
        return await submit_consumer_job(_CreateUsageSnapshotJob(
            provider=Provider.CODEX,
            fields=fields,
            future=future,
        ))
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to save Codex usage snapshot: %s", e, exc_info=True)
        return None
