"""
Cron restart: re-launch Claude Code sessions that had active cron jobs.

Called at TwiCC startup (restart_all_session_crons) and at runtime when a
process with active crons dies from a non-manual cause (_restart_crons_for_session
in ClaudeCodeAgentManager). Both paths use the same restart_session_crons() function.

The cross-provider boot cleanup of stale :class:`ProcessRun` rows lives in
:mod:`twicc.agent.process_run_cleanup` and runs *before* this module is
invoked; by the time :func:`_prepare_restarts` reads the table, the only
surviving Claude Code rows are those whose
:meth:`ClaudeCodeHelpers.should_keep_dead_process_run` returned ``True``
(= rows that still have :class:`SessionCron` rows attached).
"""

import asyncio
import logging
import os
from collections.abc import Iterator
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider

logger = logging.getLogger(__name__)

RETRY_ESCALATION = [0, 5, 15, 30, 60, 120]
MAX_RETRY_DELAY = 300  # 5 minutes cap between attempts


class PrepareCronRestartsJob(NamedTuple):
    """Async-queue job to run :func:`_prepare_restarts` on the DB writer.

    Claude Code-specific (this provider's helper handles it in
    :meth:`ClaudeCodeHelpers.try_handle_async_job`). Pushed onto
    :attr:`db_writer._async_queue` via :func:`submit_async_job`.

    Today :func:`_prepare_restarts` is a near-pure read (it only deletes
    rows whose :class:`Session` was removed since the boot cleanup ran),
    but it remains routed through the DB writer to keep its read+delete
    pass serialised with every other writer.

    ``provider`` is fixed (this job is CC-only) but kept so the job
    follows the same convention as every other async-queue job — the DB
    writer's :func:`_settle_async_job` reads it for logging.
    ``future`` resolves to the list of session ids the caller should
    restart.
    """

    future: asyncio.Future  # → list[str] (session ids)
    provider: Provider = Provider.CLAUDE_CODE


def _apply_prepare_cron_restarts_job(job: PrepareCronRestartsJob) -> list[str]:
    """Run :func:`_prepare_restarts` inside ``transaction.atomic``.

    Sync — runs in a worker thread via ``sync_to_async`` inside
    :func:`db_writer._settle_async_job`. The atomic block keeps the
    Session-existence DELETE pass serialised with every other DB writer
    write, even though most of the cross-provider consolidation has
    already happened in :mod:`twicc.agent.process_run_cleanup` at boot.
    """
    with transaction.atomic():
        return _prepare_restarts()


def _retry_delays(initial_delay: int = 0) -> Iterator[int]:
    """Yield retry delays infinitely: initial_delay, then escalation (skipping ≤), then MAX_RETRY_DELAY forever."""
    yield initial_delay
    for delay in RETRY_ESCALATION:
        # Skip delays ≤ initial_delay to keep the sequence monotonically increasing
        # (e.g., with initial_delay=10: skip 0, 5 → yield 15, 30, 60, 120)
        if delay <= initial_delay:
            continue
        yield delay
    while True:
        yield MAX_RETRY_DELAY


def _collect_restart_data(session_id: str) -> dict | None:
    """Collect restart data for a single session (synchronous, runs in thread).

    Returns a dict with keys matching send_to_session() kwargs (minus text)
    plus crons_data for message building. Returns None if restart not possible
    (no active crons, session not found, or cwd missing).
    """
    from twicc.core.enums import Provider
    from twicc.core.models import Session, SessionCron
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    active_crons = list(SessionCron.active_for_session(session_id, Provider.CLAUDE_CODE))
    if not active_crons:
        return None

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("Cron restart for session %s: session not found in DB", session_id)
        return None

    cwd = session.cwd
    if not cwd or not os.path.isdir(cwd):
        logger.warning("Cron restart for session %s: cwd '%s' does not exist on disk", session_id, cwd)
        return None

    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    from twicc.project_hierarchy import project_agent_defaults
    project_defaults = project_agent_defaults(session.project_id, helpers.provider.value)
    agent_settings = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(
            AgentSettings.from_session(session), project_defaults=project_defaults,
        ),
    )

    return {
        "session_id": session_id,
        "project_id": session.project_id,
        "cwd": cwd,
        "crons_data": [
            {"cron_expr": c.cron_expr, "recurring": c.recurring, "prompt": c.prompt}
            for c in active_crons
        ],
        "settings": agent_settings,
    }


async def restart_all_session_crons(stop_event: asyncio.Event) -> None:
    """Scan ProcessRun table and restart all sessions with persisted crons.

    Steps:
    1. Clean up orphan/stale process runs (routed through the DB writer)
    2. Launch restart_session_crons() in parallel for each session with active crons
    """
    from django.conf import settings

    if not settings.CRON_AUTO_RESTART:
        logger.info("Cron auto-restart disabled (TWICC_NO_CRON_RESTART is set)")
        return

    from twicc.providers.db_writer import submit_async_job

    future = asyncio.get_running_loop().create_future()
    try:
        session_ids = await submit_async_job(PrepareCronRestartsJob(future=future))
    except Exception as e:
        logger.error("Cron restart prepare failed via DB writer: %s", e, exc_info=True)
        return

    if not session_ids:
        logger.info("No cron jobs to restart")
        return

    logger.info("Restarting cron jobs for %d session(s)", len(session_ids))

    tasks = [
        restart_session_crons(sid, stop_event=stop_event)
        for sid in session_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = 0
    cancelled = 0
    for result in results:
        if result is None:
            succeeded += 1
        elif isinstance(result, asyncio.CancelledError):
            cancelled += 1
        elif isinstance(result, BaseException):
            logger.error("Unexpected error in cron restart: %s", result)

    logger.info(
        "Cron restart complete: %d succeeded, %d cancelled (shutdown)",
        succeeded, cancelled,
    )


def _prepare_restarts() -> list[str]:
    """Return session IDs for Claude Code ProcessRuns eligible for cron restart.

    Called in asyncio.to_thread from restart_all_session_crons(). By the
    time this runs, the cross-provider boot cleanup in
    :mod:`twicc.agent.process_run_cleanup` has already consolidated the
    :class:`ProcessRun` table — orphan rows are gone, per-session duplicates
    are collapsed to a single oldest row, and only rows the Claude Code
    helper deemed worth keeping (= rows that still have :class:`SessionCron`
    rows attached) survive on the CC slice.

    The single remaining concern here is sessions whose JSONL was deleted
    on disk between TwiCC instances. The boot cleanup keeps them because
    their cron rows still exist; the per-session initial sync removes the
    matching :class:`Session` row but doesn't know about ProcessRun, so we
    catch that case here and delete the now-pointless :class:`ProcessRun`
    (cascading its crons) before returning the surviving session ids.
    """
    from twicc.agent.states import AgentState
    from twicc.core.enums import Provider
    from twicc.core.models import ProcessRun, Session

    cc_provider = Provider.CLAUDE_CODE.value

    session_ids: list[str] = []
    for process_run in (
        ProcessRun.objects
        .filter(provider=cc_provider, state=AgentState.DEAD.value)
        .order_by("started_at")
    ):
        session_id = process_run.session_id

        # Validate session exists (clean up if JSONL was deleted)
        if not Session.objects.filter(id=session_id).exists():
            process_run.delete()
            logger.warning(
                "Session %s: not found in DB, deleted process run %s",
                session_id, process_run.pk,
            )
            continue

        session_ids.append(session_id)

    return session_ids


async def restart_session_crons(
    session_id: str,
    *,
    stop_event: asyncio.Event,
    initial_delay: int = 0,
) -> None:
    """Restart cron jobs for a single session with infinite retry.

    On each attempt: collects fresh data from DB, sends restart message to Claude,
    waits for the first USER_TURN to confirm success. Retries indefinitely with
    capped exponential backoff until success, cancellation (stop_event), or all
    crons have expired (nothing left to restart).

    Used identically by startup (restart_all_session_crons) and runtime
    (_restart_crons_for_session in ClaudeCodeAgentManager).
    """
    from twicc.agent import AgentState
    from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

    manager = get_claude_code_agent_manager()
    delays = _retry_delays(initial_delay)
    attempt = 0

    while True:
        delay = next(delays)
        attempt += 1

        if delay > 0:
            logger.info(
                "Cron restart for session %s: attempt %d in %ds",
                session_id, attempt, delay,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                logger.info(
                    "Cron restart for session %s: cancelled during delay (attempt %d)",
                    session_id, attempt,
                )
                return
            except asyncio.TimeoutError:
                pass  # Normal: delay elapsed, time to retry

        # Collect fresh data on each attempt (crons may expire, settings may change)
        restart_data = await asyncio.to_thread(_collect_restart_data, session_id)
        if restart_data is None:
            logger.info(
                "Cron restart for session %s: no restart data available, stopping (attempt %d)",
                session_id, attempt,
            )
            return

        crons_data = restart_data.pop("crons_data")
        message = _build_restart_message(crons_data)

        try:
            await manager.send_to_session(**restart_data, text=message, cancel_cron_restart=False)

            process = manager._agents.get(session_id)
            if process is None:
                logger.warning(
                    "Cron restart for session %s: process not found after send_to_session (attempt %d)",
                    session_id, attempt,
                )
                continue

            if process.state == AgentState.DEAD:
                logger.warning(
                    "Cron restart for session %s: process died immediately (attempt %d)",
                    session_id, attempt,
                )
                continue

            # Wait for first USER_TURN (success) or DEAD (failure)
            try:
                await asyncio.wait_for(
                    process._first_turn_done_event.wait(),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Cron restart for session %s: timeout waiting for USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                await manager.kill_agent(session_id, reason="cron_restart_timeout")
                continue

            if process._first_user_turn_reached:
                logger.info("Successfully restarted crons for session %s (attempt %d)", session_id, attempt)
                return
            else:
                logger.warning(
                    "Cron restart for session %s: process died before USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                continue

        except Exception as e:
            logger.error(
                "Cron restart for session %s: unexpected error (attempt %d): %s",
                session_id, attempt, e,
            )
            continue


def _format_cron_description(cron: dict, *, cron_id_to_delete: str | None = None) -> str:
    """Format a single cron's details for inclusion in a message.

    Args:
        cron: Dict with "cron_expr", "recurring", "prompt" keys.
        cron_id_to_delete: If provided, adds an "ID to delete" line (for renewal messages).
    """
    lines = []
    if cron_id_to_delete:
        lines.append(f"**ID to delete**: `{cron_id_to_delete}`")
    schedule = f'**Schedule**: `{cron["cron_expr"]}`'
    if cron["recurring"]:
        schedule += " (recurring)"
    lines.append(schedule)
    lines.append("**Prompt**:")
    lines.append("<cron-prompt>")
    lines.append(cron["prompt"])
    lines.append("</cron-prompt>")
    return "\n".join(lines)


def _build_cron_descriptions(crons_data: list[dict], *, with_cron_ids: bool = False) -> str:
    """Build the formatted block of cron descriptions separated by ---."""
    parts = ["---\n"]
    for cron in crons_data:
        cron_id_to_delete = cron.get("cron_id") if with_cron_ids else None
        parts.append(_format_cron_description(cron, cron_id_to_delete=cron_id_to_delete))
        parts.append("\n---\n")
    return "\n".join(parts)


def _build_restart_message(crons_data: list[dict]) -> str:
    """Build the user message asking Claude to recreate cron jobs.

    Used when the process is dead and crons need to be recreated from scratch.
    No deletion needed since the CLI crons are already gone.
    """
    if len(crons_data) == 1:
        header = (
            "This session was just resumed, so we lost our previous cron job, "
            "please recreate it using CronCreate:"
        )
    else:
        header = (
            "This session was just resumed, so we lost our previous cron jobs, "
            "please recreate each of them using CronCreate:"
        )

    descriptions = _build_cron_descriptions(crons_data)

    return (
        f"<twicc-cron-restart>\n"
        f"{header}\n\n{descriptions}\n\n"
        f"Use the exact schedule and prompt shown above for each CronCreate call.\n\n"
        f"Do not say anything other than a short sentence acknowledging the number of crons recreated.\n"
        f"</twicc-cron-restart>"
    )


def _build_renewal_message(crons_data: list[dict]) -> str:
    """Build the user message asking Claude to delete and recreate expired cron jobs.

    Used when the process is alive but crons have reached their 7-day expiry.
    The CLI may or may not have auto-deleted them yet, so we ask Claude to
    delete them first (if they still exist) before recreating.

    Each dict in crons_data must include a "cron_id" key with the CLI cron ID.
    """
    if len(crons_data) == 1:
        header = (
            "A cron job may have automatically expired. "
            "Please delete it using CronDelete if it still exists, "
            "then recreate it using CronCreate:"
        )
    else:
        header = (
            "The following cron jobs may have automatically expired. "
            "For each one, delete it using CronDelete if it still exists, "
            "then recreate it using CronCreate:"
        )

    descriptions = _build_cron_descriptions(crons_data, with_cron_ids=True)

    return (
        f"<twicc-cron-renewal>\n"
        f"{header}\n\n{descriptions}\n\n"
        f"Use the exact schedule and prompt shown above for each CronCreate call.\n\n"
        f"Do not say anything other than a short sentence acknowledging the number of crons recreated.\n"
        f"</twicc-cron-renewal>"
    )
