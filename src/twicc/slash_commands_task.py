"""
Background task for periodic slash command discovery.

Scans the filesystem every 5 minutes to discover slash commands from
user-level, project-level, and plugin sources, and synchronizes them
to the SlashCommand database table.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Stop event for slash commands sync task
_stop_event: asyncio.Event | None = None

# Interval: 5 minutes
SYNC_INTERVAL = 5 * 60


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event for the slash commands sync task."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_slash_commands_task() -> None:
    """Signal the slash commands sync task to stop."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()


def _sync_to_database() -> dict[str, int]:
    """Discover all slash commands and sync them to the database.

    Returns a dict with keys: created, updated, deleted, unchanged.
    """
    from twicc.core.models import Project, SlashCommand, SlashCommandSource
    from twicc.slash_commands import (
        DiscoveredCommand,
        discover_global_commands,
        discover_project_commands,
        read_plugin_entries,
    )

    stats = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}

    plugin_entries = read_plugin_entries()

    # --- 1. Discover global commands ---
    global_commands = discover_global_commands(plugin_entries=plugin_entries)

    # --- 2. Discover per-project commands ---
    # project_id -> list of discovered commands
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
                "source": cmd.source,
                "plugin_name": cmd.plugin_name,
                "description": cmd.description,
                "argument_hint": cmd.argument_hint,
            }

    for project_id, cmds in project_commands.items():
        for cmd in cmds:
            key = (project_id, cmd.name)
            if key not in desired:
                desired[key] = {
                    "source": cmd.source,
                    "plugin_name": cmd.plugin_name,
                    "description": cmd.description,
                    "argument_hint": cmd.argument_hint,
                }

    # --- 4. Load current state from database ---
    existing: dict[tuple[str | None, str], SlashCommand] = {}
    for obj in SlashCommand.objects.all():
        existing[(obj.project_id, obj.name)] = obj

    # --- 5. Diff and apply ---
    # Fields to compare for updates
    compare_fields = ("source", "plugin_name", "description", "argument_hint")

    # Delete commands no longer discovered
    to_delete_ids = []
    for key, obj in existing.items():
        if key not in desired:
            to_delete_ids.append(obj.pk)
            stats["deleted"] += 1
    if to_delete_ids:
        SlashCommand.objects.filter(pk__in=to_delete_ids).delete()

    # Create or update
    to_create: list[SlashCommand] = []
    to_update: list[SlashCommand] = []

    for key, fields in desired.items():
        project_id, name = key
        obj = existing.get(key)

        if obj is None:
            # New command
            to_create.append(SlashCommand(
                project_id=project_id,
                name=name,
                **fields,
            ))
            stats["created"] += 1
        else:
            # Check if any field changed
            changed = False
            for field_name in compare_fields:
                if getattr(obj, field_name) != fields[field_name]:
                    changed = True
                    setattr(obj, field_name, fields[field_name])
            if changed:
                to_update.append(obj)
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

    if to_create:
        SlashCommand.objects.bulk_create(to_create)
    if to_update:
        SlashCommand.objects.bulk_update(to_update, compare_fields)

    return stats


async def start_slash_commands_task() -> None:
    """Background task that periodically discovers and syncs slash commands.

    Runs until stop event is set:
    - Syncs immediately on startup
    - Then waits SYNC_INTERVAL before the next sync
    - Handles graceful shutdown via stop event
    """
    stop_event = get_stop_event()

    logger.info("Slash commands sync task started")

    while not stop_event.is_set():
        try:
            stats = await asyncio.to_thread(_sync_to_database)
            if stats["created"] or stats["updated"] or stats["deleted"]:
                logger.info(
                    "Slash commands sync: %d created, %d updated, %d deleted, %d unchanged",
                    stats["created"], stats["updated"], stats["deleted"], stats["unchanged"],
                )
            else:
                logger.debug(
                    "Slash commands sync: %d unchanged",
                    stats["unchanged"],
                )
        except Exception as e:
            logger.error("Slash commands sync failed: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SYNC_INTERVAL)
        except asyncio.TimeoutError:
            pass

    logger.info("Slash commands sync task stopped")
