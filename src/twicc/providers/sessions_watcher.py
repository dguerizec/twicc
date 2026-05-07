"""
Provider-agnostic file watcher for JSONL session files.

Each provider stores session files under a native directory layout below its
own ``projects_dir`` (e.g. Claude Code uses ``~/.claude/projects/``). A
provider-specific subclass of :class:`BaseSessionsWatcher` plugs that layout
in by overriding :meth:`BaseSessionsWatcher.parse_jsonl_path` and the
``projects_dir`` class attribute. The base class owns the watchfiles loop,
ORM updates, WebSocket broadcasts, full-text search indexing, and the
projects-dir polling phase — all generic across providers.

The watcher does not maintain a registry: each provider's orchestrator
instantiates and starts its own subclass directly (typically via a
provider-local ``get_watcher()`` singleton helper). The compute object
returned by :meth:`BaseSessionsWatcher.get_compute` carries both
``provider`` and ``compute_version``, so the watcher does not need to inject
those separately.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from watchfiles import Change, awatch

import twicc.search as search
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.projects import (
    load_project_directories,
    load_project_git_roots,
    update_project_metadata as _update_project_metadata_sync,
)
from twicc.providers.helpers import AgentSettings, get_provider_helpers

if TYPE_CHECKING:
    from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate

logger = logging.getLogger(__name__)


# Polling intervals (seconds) for the "waiting for projects dir" phase.
PROJECTS_DIR_POLL_INTERVAL = 30
PROJECTS_DIR_POLL_INTERVAL_FAST = 5
# How long fast-polling stays engaged after a request.
FAST_POLL_DURATION = 30


class ParsedPath:
    """Result of parsing a JSONL file path. Generic across providers."""
    __slots__ = ('project_id', 'session_id', 'type', 'parent_session_id', 'file_path')

    def __init__(
        self,
        project_id: str,
        session_id: str,
        type: SessionType,
        file_path: str,
        parent_session_id: str | None = None,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.type = type
        self.parent_session_id = parent_session_id
        # Provider-relative path (relative to the watcher's ``projects_dir``).
        self.file_path = file_path


# ---- @sync_to_async helpers (stateless, no provider coupling) ----

@sync_to_async
def get_or_create_project(project_id: str) -> tuple[Project, bool]:
    """Get or create a project in the database."""
    return Project.objects.get_or_create(id=project_id)


@sync_to_async
def update_project_metadata(project: Project) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    _update_project_metadata_sync(project.id)


@sync_to_async
def get_project_by_id(project_id: str) -> Project | None:
    """Get a project by ID, or None if not found."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


@sync_to_async
def get_session_by_id(session_id: str) -> Session | None:
    """Get a session by ID, or None if not found."""
    try:
        return Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        return None


@sync_to_async
def check_file_has_content_async(file_path: Path) -> bool:
    """Check if a JSONL file has any valid lines (async wrapper)."""
    if not file_path.exists():
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return True
    return False


@sync_to_async
def get_session_items(session: Session, line_nums: list[int]) -> list[dict]:
    """Get full session items (with content) by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).order_by("line_num")
    return [serialize_session_item(item) for item in items]


@sync_to_async
def get_items_metadata(session: Session, line_nums: list[int]) -> list[dict]:
    """Get metadata (without content) for specific items by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).defer('content').order_by("line_num")
    return [serialize_session_item_metadata(item) for item in items]


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


@sync_to_async
def refresh_project(project: Project) -> Project:
    """Refresh project from database."""
    project.refresh_from_db()
    return project


async def broadcast_message(channel_layer, message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


class BaseSessionsWatcher:
    """Provider-agnostic file watcher for JSONL session files.

    Subclasses set :attr:`projects_dir` and implement :meth:`parse_jsonl_path`
    and :meth:`get_compute`. The base orchestrates everything else:
    incremental sync via the compute object, broadcast of session/project
    updates, full-text search indexing, and the projects-dir polling phase
    used while the directory does not yet exist.

    :meth:`request_fast_poll` and :meth:`stop_watcher` use per-instance
    events so multiple providers can run watchers in parallel without
    stepping on each other.
    """

    projects_dir: ClassVar[Path]

    def __init__(self) -> None:
        self._stop_event: asyncio.Event | None = None
        self._boost_event: asyncio.Event | None = None
        self._fast_poll_until: float = 0.0

    # ------------------------------------------------------------------
    # Provider extension surface — overridden by each subclass
    # ------------------------------------------------------------------

    def parse_jsonl_path(self, path: Path) -> ParsedPath | None:
        """Parse a JSONL file path into a :class:`ParsedPath`.

        Returns ``None`` if ``path`` does not match any known layout for
        this provider (the watcher silently skips such paths). The
        returned ``file_path`` must be the path relative to
        :attr:`projects_dir`.
        """
        raise NotImplementedError

    def get_compute(self) -> BaseSessionCompute:
        """Return the provider's compute singleton.

        Used for:

        - new-line ingestion via
          :meth:`~twicc.providers.compute_base.BaseSessionCompute.sync_session_items_from_file`,
        - reading provider metadata (:attr:`provider`,
          :attr:`compute_version`) when creating fresh ``Session`` rows
          and when looking up the matching helpers for search indexing.
        """
        raise NotImplementedError

    async def _after_tool_result_broadcast(self, update: ToolResultUpdate) -> None:
        """Hook fired right after a ``tool_state`` broadcast.

        Default implementation is a no-op. Claude Code overrides this to
        clean up the agent manager's ``_active_tools`` registry when a
        ``tool_result`` appears in JSONL but no PostToolUse hook ever
        fired (e.g. CLI-side validation rejection).
        """
        return None

    # ------------------------------------------------------------------
    # Per-instance event accessors
    # ------------------------------------------------------------------

    def get_stop_event(self) -> asyncio.Event:
        """Get or create the stop event for this watcher instance."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    def get_boost_event(self) -> asyncio.Event:
        """Get or create the boost event used to wake the polling loop."""
        if self._boost_event is None:
            self._boost_event = asyncio.Event()
        return self._boost_event

    def request_fast_poll(self, duration: float = FAST_POLL_DURATION) -> None:
        """
        Shorten the projects-dir poll interval for ``duration`` seconds.

        Called when something likely to create the watcher's
        :attr:`projects_dir` is about to happen (e.g. starting a Claude
        Code SDK session). Cheap no-op when the watcher is already past
        the polling phase — the boost event is consumed only by that
        phase's wait loop.
        """
        deadline = time.monotonic() + duration
        if deadline > self._fast_poll_until:
            self._fast_poll_until = deadline
        self.get_boost_event().set()

    def stop_watcher(self) -> None:
        """Signal this watcher instance to stop."""
        if self._stop_event is not None:
            self._stop_event.set()
        # Wake the polling loop if it's currently sleeping.
        if self._boost_event is not None:
            self._boost_event.set()

    # ------------------------------------------------------------------
    # Session creation (uses provider/compute_version from the compute)
    # ------------------------------------------------------------------

    def create_session_sync(
        self,
        parsed: ParsedPath,
        project: Project,
        parent_session: Session | None = None,
        agent_settings: AgentSettings | None = None,
    ) -> Session:
        """Create a session or subagent in the database (sync).

        Wrap with :func:`sync_to_async` at the call site. ``provider`` and
        ``compute_version`` are read from the compute singleton, so the
        owning provider stays in one place.
        """
        compute = self.get_compute()
        if parsed.type == SessionType.SUBAGENT:
            if parent_session is None:
                raise ValueError("parent_session is required for subagents")
            return Session.objects.create(
                id=parsed.session_id,
                project=project,
                provider=compute.provider,
                file_path=parsed.file_path,
                type=SessionType.SUBAGENT,
                parent_session=parent_session,
                agent_id=parsed.session_id,
                compute_version=compute.compute_version,
            )
        kwargs: dict = dict(
            id=parsed.session_id,
            project=project,
            provider=compute.provider,
            file_path=parsed.file_path,
            compute_version=compute.compute_version,
        )
        if agent_settings is not None:
            for field, value in agent_settings._asdict().items():
                if value is not None:
                    kwargs[field] = value
        return Session.objects.create(**kwargs)

    # ------------------------------------------------------------------
    # Full-text search indexing
    # ------------------------------------------------------------------

    async def _index_new_items_for_search(
        self, session: Session, line_nums: list[int]
    ) -> None:
        """Index new session items for full-text search.

        Only indexes ``user_message`` and ``assistant_message`` items.
        Errors are caught and logged to never crash the watcher.
        """
        try:
            if not search.is_initialized():
                return

            items = await sync_to_async(
                lambda: list(
                    SessionItem.objects.filter(
                        session=session,
                        line_num__in=line_nums,
                        kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
                    )
                )
            )()

            helpers = get_provider_helpers(self.get_compute().provider)
            indexed_count = 0
            for item in items:
                text = helpers.extract_indexable_text(item)
                if text:
                    await asyncio.to_thread(
                        search.index_document,
                        session.id,
                        session.project_id,
                        item.line_num,
                        text,
                        "user" if item.kind == ItemKind.USER_MESSAGE else "assistant",
                        item.timestamp,
                        session.archived,
                    )
                    indexed_count += 1

            if indexed_count > 0:
                await asyncio.to_thread(search.commit)
        except Exception:
            logger.exception(
                "Error indexing session items for search (session=%s)", session.id
            )

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    async def sync_project_and_broadcast(
        self,
        path: Path,
        change_type: Change,
        channel_layer,
    ) -> None:
        """
        Handle a project directory being created or deleted.

        Projects are NOT created eagerly here. They are created lazily when the
        first session with content appears (in ``sync_and_broadcast``). This
        avoids polluting the project list with empty folders (e.g. folders
        left behind after Claude sublimates old sessions).

        This handler only updates the stale flag on existing projects.
        Stale is based on working directory existence, not the provider folder.
        """
        project = await get_project_by_id(path.name)
        if project is None:
            return

        should_be_stale = (
            project.directory is not None and not os.path.isdir(project.directory)
        )
        if project.stale != should_be_stale:
            project.stale = should_be_stale
            await sync_to_async(project.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })

    async def sync_and_broadcast(
        self,
        path: Path,
        parsed: ParsedPath,
        change_type: Change,
        channel_layer,
    ) -> None:
        """
        Handle a session or subagent file change.

        Synchronizes with the database and broadcasts updates via WebSocket.
        Empty files (0 lines) are ignored and not created in the database.
        """
        compute = self.get_compute()
        is_subagent = parsed.type == SessionType.SUBAGENT

        # For subagents, verify parent session exists
        parent_session: Session | None = None
        if is_subagent:
            parent_session = await get_session_by_id(parsed.parent_session_id)
            if parent_session is None:
                # Parent session not yet synced, skip for now
                logger.debug(
                    f"Skipping subagent {parsed.session_id}: "
                    f"parent session {parsed.parent_session_id} not found"
                )
                return

        if change_type == Change.deleted:
            # File deleted - mark as stale
            session = await get_session_by_id(parsed.session_id)
            if session and not session.stale:
                session.stale = True
                await sync_to_async(session.save)(update_fields=["stale"])
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(session),
                })
                # Update project metadata (includes total_cost which changes for subagents too)
                project = await get_project_by_id(parsed.project_id)
                if project:
                    await update_project_metadata(project)
                    project = await refresh_project(project)
                    await broadcast_message(channel_layer, {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    })
            return

        # Check if session already exists in DB
        session = await get_session_by_id(parsed.session_id)

        # Ensure project exists first
        project, project_created = await get_or_create_project(parsed.project_id)
        if project_created:
            await broadcast_message(channel_layer, {
                "type": "project_added",
                "project": serialize_project(project),
            })

        # Track whether this session was just created via TwiCC (had pending settings).
        # Used below to broadcast an early session_updated even before the user message
        # appears in the JSONL, so the frontend can drop the draft flag immediately.
        pending_agent_settings: AgentSettings | None = None

        if session is None:
            # New file - check if it has content before creating
            has_content = await check_file_has_content_async(path)
            if not has_content:
                # Empty file (0 lines) - ignore completely
                return

            # Create session (regular or subagent)
            # Pop any pending settings set by the WS handler for new sessions
            from twicc.pending_agent_settings import pop_pending_agent_settings

            pending_agent_settings = pop_pending_agent_settings(parsed.session_id)
            session = await sync_to_async(self.create_session_sync)(
                parsed, project, parent_session, pending_agent_settings,
            )

        old_title = session.title
        new_line_nums, modified_line_nums, agent_link_updates, tool_result_updates, agent_stopped_updates = await sync_to_async(
            compute.sync_session_items_from_file
        )(session, path)
        title_changed = session.title != old_title

        if new_line_nums:
            # Refresh session to get computed values
            session = await refresh_session(session)

            # Only broadcast if session has user messages — empty sessions (e.g. just
            # system/metadata lines) stay silent in DB until a user message arrives.
            # Exception: TwiCC-initiated sessions (identified by having had pending
            # settings) get an early session_updated so the frontend drops the draft
            # flag immediately, without waiting for the user message to appear in JSONL.
            if session.user_message_count > 0 or pending_agent_settings is not None:
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(session),
                })

            if session.user_message_count > 0:
                # Broadcast new items (with updated metadata of pre-existing items if any)
                new_items = await get_session_items(session, new_line_nums)
                if new_items:
                    message = {
                        "type": "session_items_added",
                        "session_id": parsed.session_id,
                        "project_id": parsed.project_id,
                        "parent_session_id": parsed.parent_session_id,
                        "items": new_items,
                    }
                    if modified_line_nums:
                        updated_metadata = await get_items_metadata(session, modified_line_nums)
                        if updated_metadata:
                            message["updated_metadata"] = updated_metadata
                    await broadcast_message(channel_layer, message)

                # For subagents, broadcast parent session update (costs have changed)
                if is_subagent and parent_session:
                    parent_session = await refresh_session(parent_session)
                    await broadcast_message(channel_layer, {
                        "type": "session_updated",
                        "session": serialize_session(parent_session),
                    })

                # Update project metadata (includes total_cost which changes for subagents too)
                await update_project_metadata(project)
                project = await refresh_project(project)
                await broadcast_message(channel_layer, {
                    "type": "project_updated",
                    "project": serialize_project(project),
                })

                # Broadcast agent link state changes (subagent linked)
                for update in agent_link_updates:
                    await broadcast_message(channel_layer, {
                        "type": "agent_link_created",
                        "parent_session_id": update.parent_session_id,
                        "agent_session_id": update.agent_id,
                        "tool_use_id": update.tool_use_id,
                        "tool_use_line_num": update.tool_use_line_num,
                        "is_background": update.is_background,
                        "started_at": update.started_at.isoformat() if update.started_at else None,
                        "project_id": parsed.project_id,
                    })

                # Broadcast tool result state changes
                for update in tool_result_updates:
                    await broadcast_message(channel_layer, {
                        "type": "tool_state",
                        "session_id": update.session_id,
                        "tool_use_id": update.tool_use_id,
                        "result_count": update.result_count,
                        "completed_at": update.completed_at.isoformat() if update.completed_at else None,
                        "extra": update.extra,
                        "error": update.error,
                        "tool_result_line_num": update.tool_result_line_num,
                    })
                    await self._after_tool_result_broadcast(update)

                # Broadcast session_updated for subagents that naturally finished
                for stopped in agent_stopped_updates:
                    stopped_session = await get_session_by_id(stopped.agent_session_id)
                    if stopped_session:
                        await broadcast_message(channel_layer, {
                            "type": "session_updated",
                            "session": serialize_session(stopped_session),
                        })

                # Index for full-text search (sessions only, not subagents)
                if not is_subagent:
                    if title_changed:
                        # Title changed — full session re-index (Tantivy can only delete by session_id,
                        # not by session_id + from_role, so we must re-index everything)
                        try:
                            await asyncio.to_thread(search.reindex_session, session.id)
                        except Exception:
                            logger.exception(
                                "Error re-indexing session for search after title change (session=%s)",
                                session.id,
                            )
                    else:
                        await self._index_new_items_for_search(session, new_line_nums)

                    # Mark session as indexed so the background task doesn't re-index it at next startup
                    from django.conf import settings as _settings
                    if session.search_version != _settings.CURRENT_SEARCH_VERSION:
                        session.search_version = _settings.CURRENT_SEARCH_VERSION
                        await sync_to_async(session.save)(update_fields=["search_version"])

        elif session.stale:
            # File reappeared - unstale
            session.stale = False
            await sync_to_async(session.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

        # Auto-add newly created project to workspaces whose patterns match its directory.
        if project_created:
            project = await refresh_project(project)
            if project.directory:
                from twicc.workspaces import auto_add_project_to_workspaces

                await auto_add_project_to_workspaces(project.id, project.directory)

    # ------------------------------------------------------------------
    # Polling phase + entry point
    # ------------------------------------------------------------------

    async def _wait_for_projects_dir(self) -> bool:
        """
        Poll until :attr:`projects_dir` exists or shutdown is requested.

        The interval is normally :data:`PROJECTS_DIR_POLL_INTERVAL` seconds,
        but drops to :data:`PROJECTS_DIR_POLL_INTERVAL_FAST` while a
        fast-poll request is active (typically right after a Claude Code
        session start signal).

        Returns True if the directory appeared, False if shutdown was signaled.
        """
        stop_event = self.get_stop_event()
        boost_event = self.get_boost_event()
        while not self.projects_dir.exists():
            boost_event.clear()
            fast = time.monotonic() < self._fast_poll_until
            timeout = PROJECTS_DIR_POLL_INTERVAL_FAST if fast else PROJECTS_DIR_POLL_INTERVAL

            waiters = [
                asyncio.create_task(stop_event.wait()),
                asyncio.create_task(boost_event.wait()),
            ]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
            finally:
                for w in waiters:
                    w.cancel()
                for w in waiters:
                    try:
                        await w
                    except (asyncio.CancelledError, Exception):
                        pass

            if stop_event.is_set():
                return False
        return True

    async def start_watcher(self) -> None:
        """
        Start the file watcher for this provider's :attr:`projects_dir`.

        Monitors all changes recursively and dispatches to appropriate handlers.
        If the projects directory doesn't exist yet, polls until it appears
        (e.g. user hasn't used the provider yet). The poll interval drops from
        :data:`PROJECTS_DIR_POLL_INTERVAL` to
        :data:`PROJECTS_DIR_POLL_INTERVAL_FAST` after :meth:`request_fast_poll`
        is called — typically right before a session-start that's about to
        create the directory.
        """
        channel_layer = get_channel_layer()
        projects_dir = self.projects_dir
        stop_event = self.get_stop_event()

        if not projects_dir.exists():
            logger.info(
                "Projects directory does not exist yet: %s — waiting for it to appear",
                projects_dir,
            )
            appeared = await self._wait_for_projects_dir()
            if not appeared:
                logger.info("Watcher stopped while waiting for projects directory")
                return
            logger.info("Projects directory appeared: %s", projects_dir)

        # Load project caches at startup
        await sync_to_async(load_project_directories)()
        await sync_to_async(load_project_git_roots)()

        logger.info(f"Starting file watcher on: {projects_dir}")

        async for changes in awatch(projects_dir, stop_event=stop_event):
            for change_type, path_str in changes:
                try:
                    path = Path(path_str)

                    # Handle project directories (direct children of projects_dir)
                    if path.parent == projects_dir and (path.is_dir() or change_type == Change.deleted):
                        await self.sync_project_and_broadcast(path, change_type, channel_layer)
                        continue

                    # Skip non-jsonl files
                    if not path_str.endswith(".jsonl"):
                        continue

                    # Parse path to determine type (session or subagent)
                    parsed = self.parse_jsonl_path(path)
                    if parsed is None:
                        # Invalid path — silently skip
                        continue

                    # Sync and broadcast (works for both sessions and subagents)
                    await self.sync_and_broadcast(path, parsed, change_type, channel_layer)
                except Exception:
                    logger.exception("Error processing watcher change %s on %s", change_type, path_str)
