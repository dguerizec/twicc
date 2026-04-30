"""
ASGI application configuration with WebSocket routing.

Provides HTTP and WebSocket protocol routing, with the UpdatesConsumer
handling real-time updates on the /ws/ endpoint. Also handles agent-related
messages for sending messages to Claude sessions.
"""

import asyncio
import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from blacknoise import BlackNoise
from packaging.version import InvalidVersion, Version
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.sessions import SessionMiddlewareStack
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.urls import path

from twicc.agent import AgentInfo, serialize_agent_info
from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.manager import get_claude_agent_manager
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler
from twicc.synced_settings import _settings_lock, prepare_settings_for_client, read_synced_settings, write_synced_settings
from twicc.workspaces import read_workspaces, write_workspaces
from twicc.message_snippets import read_message_snippets_config, write_message_snippets_config
from twicc.terminal_config import read_terminal_config, write_terminal_config
from twicc.providers.claude_code.usage_task import get_usage_message_for_connection
from twicc.terminal import terminal_application

logger = logging.getLogger(__name__)

# WebSocket close code for authentication failure.
# 4000-4999 range is reserved for application use by the WebSocket spec.
WS_CLOSE_AUTH_FAILURE = 4001


@sync_to_async
def get_project_directory(project_id: str) -> str | None:
    """Get the directory for a project from the database.

    Returns None if project not found or has no directory set.
    """
    from twicc.core.models import Project

    try:
        project = Project.objects.get(id=project_id)
        return project.directory
    except Project.DoesNotExist:
        return None


@sync_to_async
def session_exists(session_id: str) -> bool:
    """Check if a session exists in the database.

    Returns True if the session exists, False otherwise.
    """
    from twicc.core.models import Session

    return Session.objects.filter(id=session_id).exists()


def _get_project_display_name(project) -> str:
    """Compute a human-readable display name for a project.

    Mirrors the frontend logic in getProjectDisplayName (stores/data.js):
    1. User-defined name (project.name) takes priority
    2. Last component of the directory path
    3. Last component of the project ID (after dashes)
    """
    if project.name:
        return project.name
    if project.directory:
        parts = project.directory.rstrip("/").split("/")
        return parts[-1] if parts[-1] else project.directory
    parts = project.id.split("-")
    return parts[-1] if parts[-1] else project.id


@sync_to_async
def get_session_and_project_display(session_id: str, project_id: str) -> tuple[str | None, str | None]:
    """Get session title and project display name from the database.

    Uses select_related to fetch session + project in a single query.
    Falls back to a separate Project query if the session doesn't exist.

    Returns:
        (session_title, project_display_name) — either may be None if not found.
    """
    from twicc.core.models import Project, Session

    session_title = None
    project_name = None

    try:
        session = Session.objects.select_related("project").get(id=session_id)
        session_title = session.title
        project_name = _get_project_display_name(session.project)
    except Session.DoesNotExist:
        # Session not in DB yet (e.g. just created) — try project alone
        try:
            project = Project.objects.get(id=project_id)
            project_name = _get_project_display_name(project)
        except Project.DoesNotExist:
            pass

    return session_title, project_name


@sync_to_async
def get_bulk_session_and_project_display(
    process_infos: list[dict],
) -> dict[str, tuple[str | None, str | None]]:
    """Batch-fetch session titles and project display names for multiple processes.

    Args:
        process_infos: List of serialized process info dicts (with session_id and project_id).

    Returns:
        Dict mapping session_id → (session_title, project_display_name).
    """
    from twicc.core.models import Project, Session

    session_ids = [p["session_id"] for p in process_infos]
    project_ids = list({p["project_id"] for p in process_infos})

    # Batch fetch sessions with their projects
    sessions_by_id = {
        s.id: s
        for s in Session.objects.select_related("project").filter(id__in=session_ids)
    }

    # Batch fetch projects (for sessions not yet in DB)
    projects_by_id = {
        p.id: p
        for p in Project.objects.filter(id__in=project_ids)
    }

    result = {}
    for p in process_infos:
        sid = p["session_id"]
        pid = p["project_id"]
        session = sessions_by_id.get(sid)
        if session:
            result[sid] = (session.title, _get_project_display_name(session.project))
        else:
            project = projects_by_id.get(pid)
            result[sid] = (None, _get_project_display_name(project) if project else None)

    return result


async def _enrich_with_active_crons(message: dict, session_id: str) -> None:
    """Enrich a serialized process state dict with active crons from the database."""
    from twicc.core.models import SessionCron

    crons = await sync_to_async(
        lambda: [c.serialize() for c in SessionCron.active_for_session(session_id)]
    )()
    if crons:
        message["active_crons"] = crons


async def broadcast_process_state(info: AgentInfo) -> None:
    """Broadcast a process state change to all connected clients.

    This is the callback registered with the agent manager to handle
    state change notifications.

    When transitioning out of assistant_turn (e.g. to user_turn or dead),
    we delay the broadcast by 1 second to allow the file watcher to sync
    the final assistant message to the database and broadcast it via WebSocket
    before the frontend learns that the turn ended. This prevents a brief flash
    of an intermediate assistant message in conversation display mode.
    """
    # if info.previous_state == AgentState.ASSISTANT_TURN and info.state != AgentState.ASSISTANT_TURN:
    #     await asyncio.sleep(1)

    channel_layer = get_channel_layer()
    message = serialize_agent_info(info)
    message["type"] = "process_state"

    # Enrich with active crons from the database
    await _enrich_with_active_crons(message, info.session_id)

    # Enrich with human-readable session title and project name
    # so the frontend can display notifications without needing
    # session data in its local store.
    session_title, project_name = await get_session_and_project_display(
        info.session_id, info.project_id
    )
    if session_title is not None:
        message["session_title"] = session_title
    if project_name is not None:
        message["project_name"] = project_name

    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


# Version constants for changelog migration.
# These represent the versions before we introduced each tracking field,
# used to bootstrap the values for users upgrading from older releases.
VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN = "1.2.1"
VERSION_BEFORE_PREVIOUS_LAST_CHANGELOG_VERSION_SEEN = "1.3.0"


def _resolve_changelog_versions() -> tuple[str, str, bool]:
    """Resolve changelog tracking versions and determine if forced changelog should be shown.

    Normalizes ``lastChangelogVersionSeen`` and ``previousLastChangelogVersionSeen`` in
    settings.json, handles migration from older installs, and detects upgrades.

    Returns:
        A tuple of (previous_last_changelog_version_seen, last_changelog_version_seen, show_forced).
        ``show_forced`` is True when the user should be presented with the changelog dialog.
    """
    with _settings_lock:
        all_settings = read_synced_settings()
        last = all_settings.get("lastChangelogVersionSeen")
        previous = all_settings.get("previousLastChangelogVersionSeen")

        # --- Step 1: Normalize / initialize the two variables ---

        if not all_settings:
            # No settings or empty → first install
            all_settings["lastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["previousLastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["_version"] = all_settings.get("_version", 0) + 1
            write_synced_settings(all_settings)
            return settings.APP_VERSION, settings.APP_VERSION, False

        if last is None and previous is None:
            # Settings exist but no changelog tracking → user was on ≤ 1.2.1
            last = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
            previous = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
        elif last is not None and previous is None:
            # last exists but no previous → user was on 1.3.0 (first version with lastChangelogVersionSeen)
            previous = VERSION_BEFORE_PREVIOUS_LAST_CHANGELOG_VERSION_SEEN
        elif previous is not None and last is None:
            # Bad manual edit → force last = previous
            last = previous

        # --- Step 2: Update previous based on upgrade detection ---

        if last == previous:
            # Historical / fresh-install case → no change to previous
            pass
        elif last == settings.APP_VERSION:
            # No upgrade → no change to previous
            pass
        else:
            # New upgrade: previous != last AND last != currentVersion
            previous = last

        # --- Persist (only if values actually changed) ---

        if last != all_settings.get("lastChangelogVersionSeen") or previous != all_settings.get("previousLastChangelogVersionSeen"):
            all_settings["lastChangelogVersionSeen"] = last
            all_settings["previousLastChangelogVersionSeen"] = previous
            all_settings["_version"] = all_settings.get("_version", 0) + 1
            write_synced_settings(all_settings)

    # --- Determine if forced show is needed ---

    show_forced = False
    try:
        if Version(settings.APP_VERSION) > Version(last):
            show_forced = True
    except InvalidVersion:
        pass

    return previous, last, show_forced


class WSConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for broadcasting real-time updates.

    All connected clients join the "updates" group and receive broadcasts
    about project, session, session item changes, and process state changes.

    Handles incoming messages:
    - ping: heartbeat, responds with pong
    - send_message: send a message to a Claude session

    Provider-specific inbound messages use a ``"<provider_key>:<action>"``
    type prefix (e.g. ``claude_code:pending_request_response``) and are
    routed to the matching handler in ``_provider_handlers``. Each
    connection instantiates its own handler instances, with this consumer
    passed in so handlers can call ``send_json``, access the channel
    layer, etc.
    """

    PROVIDER_HANDLERS: dict[Provider, type] = {
        Provider.CLAUDE_CODE: ClaudeCodeWSHandler,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._provider_handlers: dict[Provider, object] = {
            key: cls(self) for key, cls in self.PROVIDER_HANDLERS.items()
        }

    async def connect(self):
        """Accept connection, add to updates group, and send active processes.

        If password protection is enabled, rejects unauthenticated WebSocket
        connections. The session is populated by SessionMiddlewareStack from
        the browser's session cookie (sent during the HTTP upgrade handshake).

        Supports an optional ``subscribe`` query parameter to filter outgoing
        messages by type. Example: ``?subscribe=process_state,active_processes``
        When set, only messages whose ``type`` matches the list are sent.
        When absent, all messages are sent (backward compatible).
        """
        # Check authentication if password protection is enabled
        if settings.TWICC_PASSWORD_HASH:
            session = self.scope.get("session", {})
            # Session.get() triggers a synchronous DB load, so we must
            # wrap it with sync_to_async in this async consumer.
            is_authenticated = await sync_to_async(session.get)("authenticated")
            if not is_authenticated:
                logger.warning("WebSocket connection rejected: not authenticated")
                # Accept first so we can send a message and a close code.
                # Closing before accept causes the close code to be lost
                # (the WebSocket handshake is never completed).
                await self.accept()
                # Send an auth_failure message as a fallback: some proxies
                # (notably Vite's dev proxy via node-http-proxy) may strip
                # the WebSocket close code, delivering 1006 instead of 4001.
                # The client handles both the message and the close code.
                await self.send_json({"type": "auth_failure"})
                await self.close(code=WS_CLOSE_AUTH_FAILURE)
                return

        # Parse optional subscribe filter from query string
        query_string = self.scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        subscribe_values = params.get("subscribe")
        if subscribe_values:
            # parse_qs returns a list of values; split each on comma and flatten
            self._subscribe_filter: set[str] | None = {
                t for value in subscribe_values for t in value.split(",") if t
            }
        else:
            self._subscribe_filter = None

        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()

        # Send server version to the client (used for auto-reload on version change)
        if self._should_send("server_version"):
            msg = {"type": "server_version", "version": settings.APP_VERSION}
            previous, last, show_forced = await sync_to_async(_resolve_changelog_versions)()
            msg["previous_last_changelog_version_seen"] = previous
            msg["last_changelog_version_seen"] = last
            if show_forced:
                msg["show_changelog_for_version"] = settings.APP_VERSION
            await self.send_json(msg)

        # Set up broadcast callback on every provider's agent manager
        # (idempotent, safe to call multiple times)
        registry = get_agent_manager_registry()
        registry.set_broadcast_callback(broadcast_process_state)

        # Send current active processes to the connecting client,
        # enriched with session titles, project names, and active crons.
        if self._should_send("active_processes"):
            processes = registry.get_active_agents()
            serialized = [serialize_agent_info(p) for p in processes]
            if serialized:
                display_info = await get_bulk_session_and_project_display(serialized)
                for proc in serialized:
                    session_title, project_name = display_info.get(proc["session_id"], (None, None))
                    if session_title is not None:
                        proc["session_title"] = session_title
                    if project_name is not None:
                        proc["project_name"] = project_name
                    # Enrich with active crons from DB
                    await _enrich_with_active_crons(proc, proc["session_id"])
            await self.send_json(
                {
                    "type": "active_processes",
                    "processes": serialized,
                }
            )

        # Send latest usage snapshot to the connecting client
        if self._should_send("usage_updated"):
            usage_message = await get_usage_message_for_connection()
            await self.send_json(usage_message)

        # Send synced settings to the connecting client
        if self._should_send("synced_settings_updated"):
            raw_settings = await sync_to_async(read_synced_settings)()
            clean_settings, version = prepare_settings_for_client(raw_settings)
            await self.send_json({"type": "synced_settings_updated", "settings": clean_settings, "version": version})

        if self._should_send("terminal_config_updated"):
            terminal_config = await sync_to_async(read_terminal_config)()
            await self.send_json({"type": "terminal_config_updated", "config": terminal_config})

        if self._should_send("message_snippets_updated"):
            message_snippets = await sync_to_async(read_message_snippets_config)()
            await self.send_json({"type": "message_snippets_updated", "config": message_snippets})

        if self._should_send("workspaces_updated"):
            workspaces = await sync_to_async(read_workspaces)()
            await self.send_json({"type": "workspaces_updated", "workspaces": workspaces.get("workspaces", [])})

        # Send current startup progress (if any phase is still active)
        if self._should_send("startup_progress"):
            from twicc.startup_progress import get_startup_progress
            for progress_msg in get_startup_progress():
                await self.send_json(progress_msg)

        # Send update available notification if a newer version is known
        if self._should_send("update_available"):
            from twicc.version_check_task import get_update_available_message
            update_msg = get_update_available_message()
            if update_msg:
                await self.send_json(update_msg)

        # Provider-specific on-connect messages (e.g. claude_code:auth_updated,
        # claude_code:settings_presets_updated, claude_code:anthropic_status).
        # Each handler yields fully-formed messages with their type already prefixed.
        for handler in self._provider_handlers.values():
            async for msg in handler.get_connect_messages():
                if self._should_send(msg.get("type", "")):
                    await self.send_json(msg)

    async def disconnect(self, close_code):
        """Remove from the updates group on disconnect."""
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.

        Provider-specific message types use a ``"<provider_key>:<action>"``
        prefix (e.g. ``claude_code:pending_request_response``) and are
        routed to the matching provider handler.

        Supported message types:
        - ping: heartbeat, responds with pong
        - send_message: send a message to a Claude session (creates new or resumes existing)
        - kill_process: kill a running Claude process
        - stop_agent: gracefully stop a running agent/task
        - suggest_title: request a title suggestion for a session
        - update_synced_settings: update synced settings and broadcast to all clients
        - session_viewed: mark a session as viewed by the user (updates last_viewed_at)
        - mark_session_read_state: explicitly mark a session as read or unread
        - list_terminals: list active tmux terminal indices for a terminal context
        - kill_terminal: kill a secondary terminal's tmux session and broadcast
        - validate_usage_file: validate a usage JSON file path (read mode) and return result
        - validate_usage_dump_path: validate a usage dump file path (write mode) and return result
        - validate_tmux_config_path: validate a tmux config file path and return result
        - changelog_seen: acknowledge that the user has seen the changelog for a version
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})

        elif msg_type == "send_message":
            await self._handle_send_message(content)

        elif msg_type == "kill_process":
            await self._handle_kill_process(content)

        elif msg_type == "stop_agent":
            await self._handle_stop_agent(content)

        elif msg_type == "user_draft_updated":
            await self._handle_user_draft_updated(content)

        elif msg_type == "suggest_title":
            # Fire-and-forget: title generation involves an SDK call (Haiku) with
            # retries that can take many seconds. Running it as a background task
            # avoids blocking the consumer — so send_message (which typically
            # follows immediately) is processed without delay.
            asyncio.create_task(self._handle_suggest_title(content))

        elif msg_type == "update_synced_settings":
            await self._handle_update_synced_settings(content)

        elif msg_type == "update_workspaces":
            await self._handle_update_workspaces(content)

        elif msg_type == "update_terminal_config":
            await self._handle_update_terminal_config(content)

        elif msg_type == "update_message_snippets":
            await self._handle_update_message_snippets(content)

        elif msg_type == "session_viewed":
            await self._handle_session_viewed(content)

        elif msg_type == "mark_session_read_state":
            await self._handle_mark_session_read_state(content)

        elif msg_type == "list_terminals":
            await self._handle_list_terminals(content)

        elif msg_type == "kill_terminal":
            await self._handle_kill_terminal(content)

        elif msg_type == "rename_terminal":
            await self._handle_rename_terminal(content)

        elif msg_type == "validate_usage_file":
            await self._handle_validate_usage_file(content)

        elif msg_type == "validate_usage_dump_path":
            await self._handle_validate_usage_dump_path(content)

        elif msg_type == "validate_tmux_config_path":
            await self._handle_validate_tmux_config_path(content)

        elif msg_type == "changelog_seen":
            await self._handle_changelog_seen(content)

        elif isinstance(msg_type, str) and ":" in msg_type:
            await self._dispatch_provider_message(msg_type, content)

        else:
            logger.warning("Unknown WebSocket message type: %r", msg_type)

    async def _dispatch_provider_message(self, msg_type: str, content: dict) -> None:
        """Route a prefixed message ``"<provider_key>:<action>"`` to the matching handler."""
        provider_key, _, action = msg_type.partition(":")
        try:
            provider = Provider(provider_key)
        except ValueError:
            logger.warning("Unknown provider key %r in message type %r", provider_key, msg_type)
            return

        handler = self._provider_handlers.get(provider)
        if handler is None:
            logger.warning("No provider handler registered for %r", provider)
            return

        handled = await handler.dispatch(action, content)
        if not handled:
            logger.warning(
                "Provider %r did not handle action %r",
                provider, action,
            )

    async def send_json(self, content, close=False):
        try:
            await super().send_json(content, close=close)
        except Exception as exc:
            logger.exception("Error sending JSON message: %s", exc)

    async def _handle_send_message(self, content: dict) -> None:
        """Handle send_message request from client.

        Expected content format:
        {
            "type": "send_message",
            "session_id": "claude-conv-xxx",
            "project_id": "proj-xyz",
            "text": "The message text",       // May be empty for settings-only updates
            "title": "Optional session title",  // Only for new sessions
            "images": [...],  // Optional: array of SDK ImageBlockParam objects
            "documents": [...]  // Optional: array of SDK DocumentBlockParam objects
        }

        This handles both new sessions and existing sessions:
        - If session exists in database: resume the session
        - If session doesn't exist: create a new session with the provided session_id

        The optional title field is only used for new sessions (drafts becoming real).
        If provided, it will be stored as a pending title and written to JSONL
        when the process becomes safe.

        The optional images and documents fields contain attachments in SDK format.

        Text may be empty for settings-only updates (changing model or permission mode
        on a live process). In that case, the SDK methods are called but no query is sent.
        """
        session_id = content.get("session_id")
        project_id = content.get("project_id")
        text = content.get("text", "")  # May be empty for settings-only updates
        title = content.get("title")  # Optional, only for new sessions
        images = content.get("images")  # Optional: SDK ImageBlockParam list
        documents = content.get("documents")  # Optional: SDK DocumentBlockParam list
        # Claude session settings: null = use global default, explicit value = forced
        permission_mode = content.get("permission_mode")
        selected_model = content.get("selected_model")
        effort = content.get("effort")
        thinking_enabled = content.get("thinking_enabled")
        claude_in_chrome = content.get("claude_in_chrome")
        context_max = content.get("context_max")

        # Validate required fields (text is allowed to be empty for settings-only updates)
        if not session_id or not project_id:
            logger.warning(
                "send_message missing required fields: session_id=%s, project_id=%s",
                session_id,
                project_id,
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": "send_message requires session_id and project_id",
                }
            )
            return

        # Validate title if provided
        if title is not None:
            from twicc.providers.claude_code.titles import validate_title

            validated_title, title_error = validate_title(title)
            if title_error:
                logger.warning(
                    "send_message: invalid title for session %s: %s",
                    session_id,
                    title_error,
                )
                await self.send_json(
                    {
                        "type": "invalid_title",
                        "session_id": session_id,
                        "title": title,
                        "error": title_error,
                    }
                )
                return
            title = validated_title

        # Get project directory from database
        cwd = await get_project_directory(project_id)
        if not cwd:
            logger.warning(
                "send_message: project %s not found or has no directory", project_id
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Project {project_id} not found or has no directory configured",
                }
            )
            return

        # Check if session exists to determine whether to create new or resume
        exists = await session_exists(session_id)

        manager = get_claude_agent_manager()
        try:
            if exists:
                # Save all Claude session settings to DB in one query.
                # Values are null (use global default) or explicit (forced).
                from twicc.core.models import Session
                settings_fields = {
                    "permission_mode": permission_mode,
                    "selected_model": selected_model,
                    "effort": effort,
                    "thinking_enabled": thinking_enabled,
                    "claude_in_chrome": claude_in_chrome,
                    "context_max": context_max,
                }
                from twicc.core.serializers import serialize_session
                await sync_to_async(
                    Session.objects.filter(id=session_id).update
                )(**settings_fields)
                # Broadcast session update so all clients see the new settings
                session_obj = await sync_to_async(Session.objects.filter(id=session_id).first)()
                if session_obj:
                    await self.channel_layer.group_send(
                        "updates",
                        {
                            "type": "broadcast",
                            "data": {
                                "type": "session_updated",
                                "session": serialize_session(session_obj),
                            },
                        },
                    )

                # If no text/attachments and no process is running, we're done:
                # settings are saved to DB and broadcast, nothing to send.
                has_content = bool(text) or bool(images) or bool(documents)
                has_process = manager.get_agent_info(session_id) is not None
                if not has_content and not has_process:
                    return

                # Resolve effective values for the process manager
                # (null → global default, so the process gets concrete values)
                from twicc.synced_settings import read_synced_settings
                defaults = read_synced_settings()
                effective = {
                    "permission_mode": permission_mode if permission_mode is not None else defaults.get("claudeCodeDefaultPermissionMode", "default"),
                    "selected_model": selected_model if selected_model is not None else defaults.get("claudeCodeDefaultModel", "opus"),
                    "effort": effort if effort is not None else defaults.get("claudeCodeDefaultEffort", "medium"),
                    "thinking_enabled": thinking_enabled if thinking_enabled is not None else defaults.get("claudeCodeDefaultThinking", True),
                    "claude_in_chrome": claude_in_chrome if claude_in_chrome is not None else defaults.get("claudeCodeDefaultClaudeInChrome", True),
                    "context_max": context_max if context_max is not None else defaults.get("claudeCodeDefaultContextMax", 200_000),
                }

                # Safety net: auto-upgrade retired models (frontend should have corrected, but just in case)
                from twicc.providers.claude_code.model_registry import enforce_1m_consistency, get_upgrade_target, is_model_retired
                if is_model_retired(effective["selected_model"]):
                    target = get_upgrade_target(effective["selected_model"])
                    if target:
                        effective["selected_model"] = target
                # Enforce 1M consistency
                effective["context_max"] = enforce_1m_consistency(effective["selected_model"], effective["context_max"])

                # Session exists: send message to it
                await manager.send_to_session(
                    session_id, project_id, cwd, text,
                    **effective,
                    images=images, documents=documents,
                )
            else:
                # New session requires text (settings-only update makes no sense here)
                if not text:
                    await self.send_json(
                        {
                            "type": "error",
                            "message": "Text is required to create a new session",
                        }
                    )
                    return

                # Session doesn't exist: create new with client-provided ID
                # Store title as pending if provided (will be written when process is safe)
                if title:
                    from twicc.providers.claude_code.titles import set_pending_title

                    set_pending_title(session_id, title)

                # Store session settings as pending (will be applied when watcher creates the session row)
                from twicc.providers.claude_code.pending_settings import set_pending

                set_pending(
                    session_id,
                    permission_mode=permission_mode,
                    selected_model=selected_model,
                    effort=effort,
                    thinking_enabled=thinking_enabled,
                    claude_in_chrome=claude_in_chrome,
                    context_max=context_max,
                )

                # Resolve effective values for process creation
                from twicc.synced_settings import read_synced_settings
                defaults = read_synced_settings()
                effective = {
                    "permission_mode": permission_mode if permission_mode is not None else defaults.get("claudeCodeDefaultPermissionMode", "default"),
                    "selected_model": selected_model if selected_model is not None else defaults.get("claudeCodeDefaultModel", "opus"),
                    "effort": effort if effort is not None else defaults.get("claudeCodeDefaultEffort", "medium"),
                    "thinking_enabled": thinking_enabled if thinking_enabled is not None else defaults.get("claudeCodeDefaultThinking", True),
                    "claude_in_chrome": claude_in_chrome if claude_in_chrome is not None else defaults.get("claudeCodeDefaultClaudeInChrome", True),
                    "context_max": context_max if context_max is not None else defaults.get("claudeCodeDefaultContextMax", 200_000),
                }

                # Safety net: auto-upgrade retired models (frontend should have corrected, but just in case)
                from twicc.providers.claude_code.model_registry import enforce_1m_consistency, get_upgrade_target, is_model_retired
                if is_model_retired(effective["selected_model"]):
                    target = get_upgrade_target(effective["selected_model"])
                    if target:
                        effective["selected_model"] = target
                # Enforce 1M consistency
                effective["context_max"] = enforce_1m_consistency(effective["selected_model"], effective["context_max"])

                await manager.create_session(
                    session_id, project_id, cwd, text,
                    **effective,
                    images=images, documents=documents,
                )
        except RuntimeError as e:
            # Process busy or other expected errors
            logger.warning("send_message failed: %s", e)
            await self.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception as e:
            # Unexpected errors - log full traceback
            logger.exception("Unexpected error in send_message")
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Failed to send message: {e}",
                }
            )

    async def _handle_kill_process(self, content: dict) -> None:
        """Handle kill_process request from client.

        Expected content format:
        {
            "type": "kill_process",
            "session_id": "claude-conv-xxx"
        }

        Only processes in STARTING or ASSISTANT_TURN state can be killed.
        The state change to DEAD will be broadcast via process_state message.
        """
        session_id = content.get("session_id")

        if not session_id:
            logger.warning("kill_process missing session_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "kill_process requires session_id",
                }
            )
            return

        registry = get_agent_manager_registry()
        killed = await registry.kill_agent(session_id, reason="manual")

        if not killed:
            # Process not found or not in killable state - not an error, just log
            logger.debug(
                "kill_process: session %s not killed (not found or not active)",
                session_id,
            )

    async def _handle_stop_agent(self, content: dict) -> None:
        """Handle stop_agent request from client.

        Expected content format:
        {
            "type": "stop_agent",
            "session_id": "claude-conv-xxx",
            "agent_id": "a6c7d21"
        }

        Calls stop_task to gracefully stop a background agent.
        """
        session_id = content.get("session_id")
        agent_id = content.get("agent_id")

        if not session_id or not agent_id:
            logger.warning("stop_agent missing session_id or agent_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "stop_agent requires session_id and agent_id",
                }
            )
            return

        manager = get_claude_agent_manager()
        stopped = await manager.stop_agent(session_id, agent_id)

        if not stopped:
            logger.error(
                "stop_agent: agent %s in session %s not stopped (not found or parent process not active)",
                agent_id,
                session_id,
            )

    async def _handle_user_draft_updated(self, content: dict) -> None:
        """Handle user_draft_updated notification from client.

        Expected content format:
        {
            "type": "user_draft_updated",
            "session_id": "claude-conv-xxx"
        }

        This is sent (debounced) when the user is actively preparing a message
        (typing text, adding images, etc.). It updates the process's last_activity
        timestamp to prevent auto-stop due to inactivity timeout.
        """
        session_id = content.get("session_id")

        if not session_id:
            # Silent ignore - this is a fire-and-forget notification
            return

        get_agent_manager_registry().touch_agent_activity(session_id)

    async def _handle_suggest_title(self, content: dict) -> None:
        """Handle title suggestion request.

        Expected content format:
        {
            "type": "suggest_title",
            "sessionId": "claude-conv-xxx",
            "systemPrompt": "System prompt with {text} placeholder",
            "prompt": "optional prompt text for draft/new sessions"
        }

        Requires systemPrompt from frontend (no fallback).

        Modes:
        - prompt provided: Use prompt directly (draft/new session or regenerate)
        - sessionId only: Fetch first message from DB (existing session)

        Always returns the prompt used for generation, so frontend can regenerate.
        """
        from twicc.providers.claude_code.title_suggest import (
            generate_title,
            get_first_user_message,
        )

        session_id = content.get("sessionId")
        system_prompt = content.get("systemPrompt")
        prompt = content.get("prompt")

        # Require both sessionId and systemPrompt
        if not session_id or not system_prompt:
            return

        # Validate systemPrompt contains {text} placeholder
        if "{text}" not in system_prompt:
            return

        # Get prompt: use provided or fetch from DB
        if not prompt:
            prompt = await get_first_user_message(session_id)

        # Generate suggestion if we have a prompt
        suggestion = None
        if prompt:
            suggestion = await generate_title(prompt, system_prompt)

        # Send result back to client (always include prompt for regeneration)
        await self.send_json({
            "type": "title_suggested",
            "sessionId": session_id,
            "suggestion": suggestion,  # Can be None
            "sourcePrompt": prompt,    # Always included for regeneration
        })

    async def _handle_update_synced_settings(self, content: dict) -> None:
        """Handle update_synced_settings request from client.

        Uses optimistic concurrency: if the client's baseVersion is behind
        the current version, the write is rejected and the client is resynced.
        """
        synced_settings = content.get("settings")
        if not isinstance(synced_settings, dict):
            return
        base_version = content.get("baseVersion")  # None for old clients

        def _merge_and_write():
            with _settings_lock:
                existing = read_synced_settings()
                current_version = existing.get("_version", 0)

                # Reject stale writes (accept if baseVersion is None — safety for rolling upgrades)
                if base_version is not None and base_version < current_version:
                    clean, ver = prepare_settings_for_client(existing)
                    return None, clean, ver  # rejected

                # Accepted — merge, increment version, write
                existing.update(synced_settings)

                # Enforce 1M consistency when claudeCodeDefaultModel changes
                if "claudeCodeDefaultModel" in synced_settings:
                    from twicc.providers.claude_code.model_registry import (
                        enforce_effort_max_consistency,
                        enforce_effort_xhigh_consistency,
                        selected_model_supports_1m,
                    )
                    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
                    new_model = existing.get("claudeCodeDefaultModel", SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultModel"])
                    if not selected_model_supports_1m(new_model) and existing.get("claudeCodeDefaultContextMax", 200_000) == 1_000_000:
                        existing["claudeCodeDefaultContextMax"] = 200_000
                    current_effort = existing.get("claudeCodeDefaultEffort")
                    adjusted_effort = enforce_effort_xhigh_consistency(
                        new_model, enforce_effort_max_consistency(new_model, current_effort)
                    )
                    if adjusted_effort != current_effort:
                        existing["claudeCodeDefaultEffort"] = adjusted_effort

                existing["_version"] = current_version + 1
                write_synced_settings(existing)
                return current_version + 1, None, None  # accepted

        new_version, reject_settings, reject_version = await sync_to_async(_merge_and_write)()

        if new_version is not None:
            # Accepted — broadcast to all clients
            await self.channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "synced_settings_updated",
                        "settings": synced_settings,
                        "version": new_version,
                    },
                },
            )
        else:
            # Rejected — resync only this client
            await self.send_json({
                "type": "synced_settings_updated",
                "settings": reject_settings,
                "version": reject_version,
            })

    async def _handle_validate_usage_file(self, content: dict) -> None:
        """Validate a usage JSON file path and return the result to the client."""
        file_path = content.get("file_path", "")
        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({"type": "usage_file_validated", "valid": False, "message": "No file path provided"})
            return

        from twicc.providers.claude_code.usage import validate_usage_file

        valid, message = await sync_to_async(validate_usage_file)(file_path.strip())
        await self.send_json({"type": "usage_file_validated", "valid": valid, "message": message})

    async def _handle_validate_usage_dump_path(self, content: dict) -> None:
        """Validate a usage dump file path and return the result to the client."""
        file_path = content.get("file_path", "")
        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({"type": "usage_dump_path_validated", "valid": False, "message": "No file path provided"})
            return

        from twicc.providers.claude_code.usage import validate_usage_dump_path

        valid, message = await sync_to_async(validate_usage_dump_path)(file_path.strip())
        await self.send_json({"type": "usage_dump_path_validated", "valid": valid, "message": message})

    async def _handle_validate_tmux_config_path(self, content: dict) -> None:
        """Validate a tmux config file path and return the result to the client."""
        file_path = content.get("file_path", "")
        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({"type": "tmux_config_path_validated", "valid": False, "message": "No file path provided"})
            return

        from twicc.terminal import validate_tmux_config_path

        valid, message = await sync_to_async(validate_tmux_config_path)(file_path.strip())
        await self.send_json({"type": "tmux_config_path_validated", "valid": valid, "message": message})

    async def _handle_update_workspaces(self, content: dict) -> None:
        """Handle workspace definitions update from a client."""
        workspaces = content.get("workspaces")
        if not isinstance(workspaces, list):
            return

        def _write():
            write_workspaces({"workspaces": workspaces})

        await sync_to_async(_write)()

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {"type": "workspaces_updated", "workspaces": workspaces},
            },
        )

    async def _handle_update_terminal_config(self, content: dict) -> None:
        """Handle terminal config update from client."""
        config = content.get("config")
        if not isinstance(config, dict):
            logger.warning("Invalid terminal config update: config is not a dict")
            return

        await sync_to_async(write_terminal_config)(config)

        # Broadcast to all connected clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_config_updated",
                    "config": config,
                },
            },
        )

    async def _handle_update_message_snippets(self, content: dict) -> None:
        """Handle message snippets config update from client."""
        config = content.get("config")
        if not isinstance(config, dict):
            logger.warning("Invalid message snippets update: config is not a dict")
            return

        await sync_to_async(write_message_snippets_config)(config)

        # Broadcast to all connected clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "message_snippets_updated",
                    "config": config,
                },
            },
        )

    async def _handle_session_viewed(self, content: dict) -> None:
        """Handle session_viewed notification from client.

        Expected content format:
        {
            "type": "session_viewed",
            "session_id": "claude-conv-xxx",
            "viewed_at": "2026-04-13T20:53:05.123Z",  // client timestamp
            "reason": "deactivated"                     // why this was sent
        }

        Updates the session's last_viewed_at timestamp and broadcasts the change
        to all connected clients (for multi-device sync).
        """
        session_id = content.get("session_id")
        if not session_id:
            return

        from django.utils import timezone

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        now = timezone.now()
        reason = content.get("reason", "unknown")
        viewed_at = content.get("viewed_at", "N/A")
        logger.debug(
            "session_viewed for %s: reason=%s, front_viewed_at=%s, back_now=%s",
            session_id, reason, viewed_at, now.isoformat(),
        )

        rows = await sync_to_async(
            Session.objects.filter(id=session_id).update
        )(last_viewed_at=now)
        if not rows:
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session:
            await self.channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )

    async def _handle_mark_session_read_state(self, content: dict) -> None:
        """Handle mark_session_read_state request from client.

        Expected content format:
        {
            "type": "mark_session_read_state",
            "session_id": "claude-conv-xxx",
            "unread": true/false
        }

        Mark as read: sets last_viewed_at = now().
        Mark as unread: clears last_viewed_at. If last_new_content_at is null
        (old sessions without backfill), sets it to now() so the unread
        comparison works.
        """
        session_id = content.get("session_id")
        unread = content.get("unread")
        if not session_id or unread is None:
            return

        from django.utils import timezone

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        from django.db.models import Case, F, Value, When

        now = timezone.now()
        if unread:
            # Clear last_viewed_at; only set last_new_content_at if it was null
            rows = await sync_to_async(
                Session.objects.filter(id=session_id).update
            )(
                last_viewed_at=None,
                last_new_content_at=Case(
                    When(last_new_content_at__isnull=True, then=Value(now)),
                    default=F('last_new_content_at'),
                ),
            )
        else:
            rows = await sync_to_async(
                Session.objects.filter(id=session_id).update
            )(last_viewed_at=now)
        if not rows:
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session:
            await self.channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )

    async def _handle_changelog_seen(self, content: dict) -> None:
        """Persist that the user has seen the changelog for the given version."""
        version = content.get("version")
        if not version:
            return

        def _persist():
            from twicc.synced_settings import _settings_lock, read_synced_settings, write_synced_settings

            with _settings_lock:
                all_settings = read_synced_settings()
                all_settings["lastChangelogVersionSeen"] = version
                all_settings["_version"] = all_settings.get("_version", 0) + 1
                write_synced_settings(all_settings)

        await sync_to_async(_persist)()

    async def _handle_list_terminals(self, data):
        """Handle list_terminals request: return active tmux terminal indices (with labels) for a terminal context."""
        terminal_context = data.get("terminal_context")
        if not terminal_context:
            await self.send_json({"type": "error", "message": "Missing terminal_context"})
            return

        from twicc.terminal import list_tmux_terminals

        terminals = await asyncio.to_thread(list_tmux_terminals, terminal_context)

        await self.send_json({
            "type": "terminal_list",
            "terminal_context": terminal_context,
            "terminals": [t.index for t in terminals],
            "labels": {str(t.index): t.label for t in terminals if t.label},
        })

    async def _handle_kill_terminal(self, data):
        """Handle kill_terminal request: kill a secondary terminal's tmux session and broadcast."""
        terminal_context = data.get("terminal_context")
        terminal_index = data.get("terminal_index")
        if not terminal_context or terminal_index is None:
            await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
            return

        # Safety: never kill the main terminal via this handler
        if terminal_index == 0:
            await self.send_json({"type": "error", "message": "Cannot kill main terminal"})
            return

        from twicc.terminal import kill_tmux_terminal

        await asyncio.to_thread(kill_tmux_terminal, terminal_context, terminal_index)

        # Broadcast to all clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_killed",
                    "terminal_context": terminal_context,
                    "terminal_index": terminal_index,
                },
            },
        )

    async def _handle_rename_terminal(self, data):
        """Handle rename_terminal request: set a display label on a terminal's tmux session.

        For tmux terminals, the label is stored as a tmux user option and
        persists across reconnections. The rename is broadcast to all clients
        for cross-device sync.
        """
        terminal_context = data.get("terminal_context")
        terminal_index = data.get("terminal_index")
        label = data.get("label", "")
        if not terminal_context or terminal_index is None:
            await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
            return

        from twicc.terminal import TERMINAL_LABEL_MAX_LENGTH, set_tmux_terminal_label

        # Sanitize: trim and truncate
        label = label.strip()[:TERMINAL_LABEL_MAX_LENGTH]

        await asyncio.to_thread(set_tmux_terminal_label, terminal_context, terminal_index, label)

        # Broadcast to all clients (including the sender, for confirmation)
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_renamed",
                    "terminal_context": terminal_context,
                    "terminal_index": terminal_index,
                    "label": label,
                },
            },
        )

    def _should_send(self, msg_type: str) -> bool:
        """Check if a message type should be sent to this client.

        Returns True if no subscribe filter is set (all messages pass)
        or if the given type is in the filter.
        """
        return not self._subscribe_filter or msg_type in self._subscribe_filter

    async def broadcast(self, event):
        """Handle broadcast events by sending data to the client.

        If the client connected with a ``subscribe`` filter, only messages
        whose type is in the filter are forwarded. Otherwise all messages
        are sent (default behavior for the TwiCC web UI).
        """
        data = event["data"]
        if not self._should_send(data.get("type", "")):
            return
        await self.send_json(data)


websocket_urlpatterns = [
    # Terminal with session context
    path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
    # Terminal with project context only (no session)
    path("ws/terminal/<str:project_id>/<int:terminal_index>/", terminal_application),
    # Terminal with no project (global/workspace context)
    path("ws/terminal/<int:terminal_index>/", terminal_application),
    path("ws/", WSConsumer.as_asgi()),
]

# Django ASGI application for HTTP requests
django_asgi_app = get_asgi_application()

# Protocol router for HTTP and WebSocket
# SessionMiddlewareStack reads the session cookie from the WebSocket
# HTTP upgrade request, making session data available in the consumer's scope.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": SessionMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)

# Serve static files via BlackNoise at the ASGI level.
application = BlackNoise(application, immutable_file_test=lambda *_: True)
application.add(settings.FRONTEND_DIST_DIR, "/static")
