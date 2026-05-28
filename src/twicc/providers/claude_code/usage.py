"""
Usage quota fetching and storage for Claude Code.

Fetches usage data from the Anthropic OAuth usage API endpoint and
stores snapshots in the database. Credentials access lives in
``.auth``.

Also provides cost estimation for quota periods by summing
SessionItem costs within the relevant time windows.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import orjson

from .auth import get_credentials, refresh_token_via_sdk
from twicc.core.models import UsageSnapshot

logger = logging.getLogger(__name__)

# Anthropic usage API endpoint
USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"

# Required headers for the usage API
USAGE_API_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/2.1.34",
}


def fetch_usage(*, refresh_token_if_needed: bool = True) -> dict | None:
    """
    Fetch usage data from the Anthropic OAuth usage API.

    On 401/403, attempts to refresh the token via a throwaway SDK call,
    then retries the fetch once if the token was actually refreshed.

    Returns:
        The raw JSON response as a dict, or None on failure.
    """
    creds = get_credentials()
    if creds is None:
        return None

    token, expires_at = creds
    headers = {
        **USAGE_API_HEADERS,
        "Authorization": f"Bearer {token}",
    }

    try:
        response = httpx.get(USAGE_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403) and refresh_token_if_needed:
            logger.warning("Usage API returned %d, attempting token refresh", e.response.status_code)
            if refresh_token_via_sdk(expires_at):
                return fetch_usage(refresh_token_if_needed=False)
            return None
        logger.warning("Usage API HTTP error: %s", e)
        return None
    except httpx.TimeoutException:
        logger.warning("Usage API request timed out")
        return None
    except Exception as e:
        logger.warning("Usage API request failed: %s", e)
        return None


def _extract_quota_fields(data: dict | None, prefix: str) -> dict:
    """
    Extract utilization and resets_at from a quota block.

    Args:
        data: The quota block (e.g., {"utilization": 12.0, "resets_at": "..."}) or None.
        prefix: Field name prefix (e.g., "five_hour").

    Returns:
        Dict with "{prefix}_utilization" and "{prefix}_resets_at" keys.
    """
    if data is None:
        return {
            f"{prefix}_utilization": None,
            f"{prefix}_resets_at": None,
        }

    resets_at_str = data.get("resets_at")
    resets_at = None
    if resets_at_str:
        try:
            resets_at = datetime.fromisoformat(resets_at_str)
        except ValueError:
            logger.warning("Failed to parse resets_at for %s: %s", prefix, resets_at_str)

    return {
        f"{prefix}_utilization": data.get("utilization"),
        f"{prefix}_resets_at": resets_at,
    }


def _build_usage_snapshot_fields(raw: dict) -> dict:
    """Translate a raw Anthropic usage API response into ``UsageSnapshot`` columns.

    Pure — no DB access. The producer (``fetch_and_save_usage``) calls this
    on the worker thread that did the HTTP fetch, then hands the result to
    the DB writer via :class:`_CreateUsageSnapshotJob` so the actual
    ``UsageSnapshot.objects.create`` happens on the single-writer path.
    """
    from twicc.core.enums import Provider

    fields = {
        "provider": Provider.CLAUDE_CODE.value,
        "fetched_at": datetime.now(timezone.utc),
        "raw_response": raw,
    }

    # Quota blocks
    for key, prefix in [
        ("five_hour", "five_hour"),
        ("seven_day", "seven_day"),
    ]:
        fields.update(_extract_quota_fields(raw.get(key), prefix))

    # Extra usage block
    extra = raw.get("extra_usage")
    if extra is not None:
        fields["extra_usage_is_enabled"] = extra.get("is_enabled", False)
        fields["extra_usage_monthly_limit"] = extra.get("monthly_limit")
        fields["extra_usage_used_credits"] = extra.get("used_credits")
        fields["extra_usage_utilization"] = extra.get("utilization")
    else:
        fields["extra_usage_is_enabled"] = False
        fields["extra_usage_monthly_limit"] = None
        fields["extra_usage_used_credits"] = None
        fields["extra_usage_utilization"] = None

    return fields


def read_usage_from_file(file_path: str) -> dict | None:
    """
    Read usage data from a local JSON file instead of calling the API.

    The file should contain raw JSON in the same format as the Anthropic
    usage API response (keys like "five_hour", "seven_day", etc.).

    Args:
        file_path: Absolute path to the JSON file.

    Returns:
        The parsed JSON dict, or None on failure.
    """
    path = Path(file_path)
    if not path.is_file():
        logger.warning("Usage JSON file not found: %s", file_path)
        return None

    try:
        return orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as e:
        logger.warning("Usage JSON file is not valid JSON: %s — %s", file_path, e)
        return None
    except OSError as e:
        logger.warning("Failed to read usage JSON file: %s — %s", file_path, e)
        return None


# Required top-level keys in the usage API response
USAGE_REQUIRED_KEYS = {"five_hour", "seven_day"}


# File-level validation is owned by ``twicc.usage.validate_usage_file`` (cross-provider envelope);
# the format check (presence of ``USAGE_REQUIRED_KEYS``) is performed by
# ``ClaudeCodeHelpers.validate_usage_file_payload``, which imports the constant above.


def dump_usage_to_file(raw: dict, file_path: str) -> None:
    """
    Write raw usage API response to a JSON file.

    Args:
        raw: The raw JSON response from the usage API.
        file_path: Absolute path to write to.
    """
    try:
        Path(file_path).write_bytes(orjson.dumps(raw, option=orjson.OPT_INDENT_2))
    except OSError as e:
        logger.warning("Failed to dump usage to file: %s — %s", file_path, e)


def _fetch_usage_raw_blocking() -> dict | None:
    """Blocking half of :func:`fetch_and_save_usage` — pure I/O, no DB.

    Reads the synced settings (file read), then either loads a previously
    dumped payload from disk or hits the Anthropic API; on a successful
    API fetch, optionally dumps the raw response to disk. Returns the raw
    payload, or ``None`` when nothing usable was fetched.
    """
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    read_enabled = settings.get("claudeCodeUsageReadFileEnabled", False)
    read_path = settings.get("claudeCodeUsageReadFilePath", "")
    dump_enabled = settings.get("claudeCodeUsageDumpFileEnabled", False)
    dump_path = settings.get("claudeCodeUsageDumpFilePath", "")

    if read_enabled and read_path:
        return read_usage_from_file(read_path)

    raw = fetch_usage()
    # Dump raw response to file if enabled (only when fetching from API)
    if raw is not None and dump_enabled and dump_path:
        dump_usage_to_file(raw, dump_path)
    return raw


async def fetch_and_save_usage() -> UsageSnapshot | None:
    """Fetch a usage payload (API or replay file) and persist it via the DB writer.

    The HTTP / file read happens on a worker thread; the parsed-fields dict
    is then routed through the DB writer via
    :class:`_CreateUsageSnapshotJob`, so the ``UsageSnapshot.objects.create``
    never races the DB writer on the SQLite write lock. Returns the created
    ``UsageSnapshot``, or ``None`` when nothing was fetched (credentials
    missing, API error, or replay file unreadable).
    """
    from twicc.core.enums import Provider
    from twicc.providers.db_writer import _CreateUsageSnapshotJob, submit_async_job

    raw = await asyncio.to_thread(_fetch_usage_raw_blocking)
    if raw is None:
        return None

    try:
        fields = _build_usage_snapshot_fields(raw)
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to parse usage snapshot fields: %s", e, exc_info=True)
        return None

    future = asyncio.get_running_loop().create_future()
    try:
        return await submit_async_job(_CreateUsageSnapshotJob(
            provider=Provider.CLAUDE_CODE,
            fields=fields,
            future=future,
        ))
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to save usage snapshot: %s", e, exc_info=True)
        return None
