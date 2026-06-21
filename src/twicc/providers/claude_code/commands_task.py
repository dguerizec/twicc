"""
Background task for periodic command discovery.

Scans the filesystem every 5 minutes to discover commands from
user-level, project-level, and plugin sources, and synchronizes them
to the Command database table.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Stop event for commands sync task
_stop_event: asyncio.Event | None = None

# Interval: 5 minutes
SYNC_INTERVAL = 5 * 60

# Activation character used by Claude Code to invoke commands.
ACTIVATION_CHAR = "/"


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event for the commands sync task."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_commands_task() -> None:
    """Signal the commands sync task to stop."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()


def _build_desired_commands() -> dict[tuple[str | None, str], dict]:
    """Discover commands from disk and return the producer-side desired state.

    Pure read-side work — scans the filesystem and reads the (non-stale)
    ``Project`` rows — so the DB-writer-routed apply only does the DB diff +
    writes. Returns the ``(project_id_or_None, name) -> fields`` map the
    DB writer's :class:`_ApplyDesiredCommandsJob` expects.

    Claude Code's hardcoded CLI built-ins live in the frontend
    ``BUILTIN_COMMANDS`` constant and never reach this table, so every row
    this task writes is non-builtin by design.
    """
    from twicc.core.models import Project
    from .commands import (
        DiscoveredCommand,
        PluginEntry,
        discover_global_commands,
        discover_project_commands,
        read_plugin_entries,
    )
    from twicc.agent.plugin import get_plugin_dir

    plugin_entries = read_plugin_entries()
    plugin_entries.append(PluginEntry(
        plugin_name="twicc",
        install_path=get_plugin_dir(),
        scope="managed",
        project_path=None,
    ))

    # --- 1. Discover global commands ---
    global_commands = discover_global_commands(plugin_entries=plugin_entries)

    # --- 2. Discover per-project commands ---
    project_commands: dict[str, list[DiscoveredCommand]] = {}
    scanned_dirs: set[Path] = set()

    projects = list(
        Project.objects.filter(directory__isnull=False, stale=False)
        .values_list("id", "directory")
    )

    for project_id, directory in projects:
        cmds = discover_project_commands(
            directory,
            plugin_entries=plugin_entries,
            scanned_dirs=scanned_dirs,
        )
        if cmds:
            project_commands[project_id] = cmds

    # --- 3. Build the desired state: (project_id_or_None, name) -> fields ---
    desired: dict[tuple[str | None, str], dict] = {}

    for cmd in global_commands:
        key = (None, cmd.name)
        # First occurrence wins (avoids duplicates from multiple plugin sources)
        if key not in desired:
            desired[key] = {
                "plugin_name": cmd.plugin_name,
                "description": cmd.description,
                "argument_hint": cmd.argument_hint,
                "is_builtin": False,
            }

    for project_id, cmds in project_commands.items():
        for cmd in cmds:
            key = (project_id, cmd.name)
            if key not in desired:
                desired[key] = {
                    "plugin_name": cmd.plugin_name,
                    "description": cmd.description,
                    "argument_hint": cmd.argument_hint,
                    "is_builtin": False,
                }

    return desired


async def _sync_to_database() -> dict[str, int]:
    """Discover commands (in a worker thread) and submit the diff/apply job.

    The blocking filesystem + DB-read work is isolated in
    :func:`_build_desired_commands` and run in a worker thread; the actual
    DB writes go through the DB writer via
    :class:`_ApplyDesiredCommandsJob`, so this task never races the DB writer
    on the SQLite write lock.

    Returns the stats dict the DB writer's apply produced.
    """
    from twicc.core.enums import Provider
    from twicc.providers.db_writer import _ApplyDesiredCommandsJob, submit_async_job

    desired = await asyncio.to_thread(_build_desired_commands)

    future = asyncio.get_running_loop().create_future()
    return await submit_async_job(_ApplyDesiredCommandsJob(
        provider=Provider.CLAUDE_CODE,
        activation_char=ACTIVATION_CHAR,
        desired=desired,
        future=future,
    ))


async def start_commands_task() -> None:
    """Background task that periodically discovers and syncs commands.

    Runs until stop event is set:
    - Syncs immediately on startup
    - Then waits SYNC_INTERVAL before the next sync
    - Handles graceful shutdown via stop event
    """
    stop_event = get_stop_event()
    # Reset for hot-restart support: clear the stop event so a relaunched
    # task isn't killed by a stale set() left by the prior shutdown.
    stop_event.clear()

    logger.info("Commands sync task started")

    while not stop_event.is_set():
        try:
            stats = await _sync_to_database()
            if stats["created"] or stats["updated"] or stats["deleted"]:
                logger.info(
                    "Commands sync: %d created, %d updated, %d deleted, %d unchanged",
                    stats["created"], stats["updated"], stats["deleted"], stats["unchanged"],
                )
            else:
                logger.debug(
                    "Commands sync: %d unchanged",
                    stats["unchanged"],
                )
        except Exception as e:
            logger.error("Commands sync failed: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SYNC_INTERVAL)
        except asyncio.TimeoutError:
            pass

    logger.info("Commands sync task stopped")
