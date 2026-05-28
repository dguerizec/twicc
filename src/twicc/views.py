"""API views and SPA catch-all for serving the frontend."""

import asyncio
import logging
import os
from bisect import bisect_left
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.utils import timezone

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
import orjson

from twicc import search
from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AgentLink, Command, DailyActivity, PinMode, Project, Session, SessionItem, SessionType, ToolResultLink, UsageSnapshot, WeeklyActivity
from twicc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.paths import path_to_project_id
from twicc.projects import register_project
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.sessions_watcher import mark_session_search_version_current
from twicc.providers.state import ProviderDisabledError, ensure_provider_running
from twicc.providers.helpers import get_provider_helpers, get_provider_helpers_registry
from twicc.terminal import kill_all_tmux_terminals
from twicc.workspaces import read_workspaces

logger = logging.getLogger(__name__)

# Number of sessions to return per page
# Set high (1000) to effectively load all sessions at once for most users,
# enabling instant client-side search/filtering without pagination complexity
SESSIONS_PAGE_SIZE = 1000

# Strong references to fire-and-forget tasks. asyncio.create_task only
# keeps a weak reference internally, so without holding the Task here the
# garbage collector can drop a still-running cleanup mid-flight. Tasks
# are added on creation and removed via ``discard`` from the
# ``add_done_callback`` so the set stays bounded.
_DETACHED_TASKS: set[asyncio.Task] = set()


async def _get_sessions_page(
    project_id: str | None,
    before_mtime: str | None,
    project_id_list: list[str] | None = None,
    pinned_only: bool = False,
    unread_only: bool = False,
    active_session_ids: list[str] | None = None,
) -> dict:
    """Get a page of sessions with pagination support.

    Args:
        project_id: Project ID to filter by, or None for all projects.
        before_mtime: Cursor for pagination - only return sessions with mtime < this value.
        project_id_list: List of project IDs to filter by (used when project_id is None).
        pinned_only: If True, include pinned sessions (any pin mode) in the union.
        unread_only: If True, include sessions with unread content (last_new_content_at
            set AND later than last_viewed_at, or last_viewed_at null) in the union.
        active_session_ids: If not None, include sessions whose id is in this list
            (typically the agent manager's active session ids) in the union.

    When more than one "sticky" flag (pinned_only / unread_only / active_session_ids)
    is set, the results are the UNION of the matching sessions — callers passing
    several flags get every session that matches at least one of them.

    Returns:
        Dict with "sessions" (list) and "has_more" (bool).
    """
    from django.db.models import F, Q

    sessions = Session.objects.filter(type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0)

    if project_id is not None:
        sessions = sessions.filter(project_id=project_id)
    elif project_id_list is not None:
        sessions = sessions.filter(project_id__in=project_id_list)

    sticky = Q()
    if pinned_only:
        sticky |= Q(pinned__isnull=False)
    if unread_only:
        sticky |= Q(last_new_content_at__isnull=False) & (
            Q(last_viewed_at__isnull=True) | Q(last_new_content_at__gt=F("last_viewed_at"))
        )
    if active_session_ids:
        sticky |= Q(id__in=active_session_ids)
    if pinned_only or unread_only or active_session_ids:
        sessions = sessions.filter(sticky)

    if before_mtime:
        sessions = sessions.filter(mtime__lt=float(before_mtime))

    # Fetch one extra to detect if there are more
    sessions = await sync_to_async(list)(
        sessions.order_by("-mtime")[: SESSIONS_PAGE_SIZE + 1]
    )

    has_more = len(sessions) > SESSIONS_PAGE_SIZE
    sessions = sessions[:SESSIONS_PAGE_SIZE]

    return {
        "sessions": [serialize_session(s) for s in sessions],
        "has_more": has_more,
    }


async def all_sessions(request):
    """GET /api/sessions/ - All sessions from all projects (paginated).

    Returns only regular sessions (not subagents).

    Query params (optional):
        before_mtime: Cursor for pagination - only return sessions older than this mtime.
        project_ids: Comma-separated list of project IDs to filter by.
        pinned: When "1"/"true", include pinned sessions (any pin mode) in the result.
        unread: When "1"/"true", include sessions with unread content in the result.
        has_process: When "1"/"true", include sessions that have an active Claude SDK
            process. Combined with pinned/unread via UNION when multiple flags are set.

    The pinned / unread / has_process flags are "sticky" filters used at app startup
    to preload cross-filter sessions that would otherwise be missing from the store
    (a single-project sidebar only loads its own project's sessions). When no such
    flag is set, the endpoint returns a regular paginated list.
    """
    before_mtime = request.GET.get("before_mtime")
    project_ids_param = request.GET.get("project_ids")
    project_id_list = project_ids_param.split(",") if project_ids_param else None
    pinned_only = request.GET.get("pinned", "").lower() in ("1", "true")
    unread_only = request.GET.get("unread", "").lower() in ("1", "true")
    has_process = request.GET.get("has_process", "").lower() in ("1", "true")

    active_session_ids: list[str] | None = None
    if has_process:
        from twicc.agent.registry import get_agent_manager_registry
        active_session_ids = [info.session_id for info in get_agent_manager_registry().get_active_agents()]

    return JsonResponse(await _get_sessions_page(
        None,
        before_mtime,
        project_id_list=project_id_list,
        pinned_only=pinned_only,
        unread_only=unread_only,
        active_session_ids=active_session_ids,
    ))


async def session_by_id(request, session_id):
    """GET /api/sessions/<session_id>/ - Fetch a single regular session by ID.

    Resolves a session when the caller does not know (or cannot trust) its
    project_id. Used by the frontend when rendering a cross-filter deep link
    (e.g. /project/A/session/sessionX where sessionX belongs to project B):
    the session view needs the session object to derive its real project,
    but the URL's project_id is the sidebar filter and may not match.

    Returns 404 if the session does not exist or is a subagent (subagents
    must be accessed via their parent's subagent route).
    """
    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    if session.parent_session_id is not None:
        raise Http404("Session not found")

    return JsonResponse(serialize_session(session))


async def project_list(request):
    """GET /api/projects/ - List all projects.
    POST /api/projects/ - Create a new project from a directory path.
    """
    if request.method == "POST":
        return await _create_project(request)

    projects = await sync_to_async(list)(Project.objects.all())
    data = [serialize_project(p) for p in projects]
    return JsonResponse(data, safe=False)


async def _create_project(request):
    """Create a new project from a directory path.

    Body: { "directory": "/absolute/path", "name": "optional", "color": "optional" }
    """
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # 1. Extract and validate directory
    directory = data.get("directory")
    if not directory or not isinstance(directory, str):
        return JsonResponse({"error": "directory is required"}, status=400)

    resolved = os.path.realpath(directory)
    if not os.path.isabs(resolved):
        return JsonResponse({"error": "Directory must be an absolute path"}, status=400)
    if not os.path.isdir(resolved):
        # If path exists but is not a directory (e.g. a file), reject outright
        if os.path.exists(resolved):
            return JsonResponse({"error": "Path exists but is not a directory"}, status=400)
        # Directory doesn't exist: create it if requested, otherwise ask the user
        if data.get("create_directory"):
            try:
                await asyncio.to_thread(os.makedirs, resolved, exist_ok=True)
            except OSError as e:
                return JsonResponse({"error": f"Failed to create directory: {e}"}, status=400)
        else:
            return JsonResponse(
                {"error": "Directory does not exist", "code": "directory_not_found"},
                status=400,
            )

    # 2. Generate project ID: all non-alphanumeric chars become dashes
    project_id = path_to_project_id(resolved)

    # 3. Check project doesn't already exist
    if await Project.objects.filter(id=project_id).aexists():
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    # 4. Validate optional name
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            name = None
        elif len(name) > 25:
            return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
        elif await Project.objects.filter(name=name).aexists():
            return JsonResponse({"error": "A project with this name already exists"}, status=400)

    # 5. Validate optional color
    color = data.get("color")
    if color is not None and not isinstance(color, str):
        color = None

    # 6. Create project — single entry point handles ``project_added``
    # broadcast and workspace auto-add. IntegrityError can still fire on a
    # ``name`` collision (the ``id`` collision path goes through the early
    # exists-check at step 3 and the get_or_create race window below).
    # ``register_project`` itself does the DB write, plus the channel-layer
    # broadcast and the workspace auto-add (which can also broadcast). The
    # whole call runs under the DB write lock; the broadcasts inside cost
    # nothing (in-process Channels) and keeping them under the lock
    # preserves the single-acquire shape of the existing helper.
    try:
        project, created = await run_under_db_write_lock(
            lambda: register_project(
                project_id,
                directory=resolved,
                name=name,
                color=color or None,
            )
        )
    except IntegrityError:
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)
    if not created:
        # Lost a race with another creator between the early exists-check
        # and now — same friendly 409 as the early-out.
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    return JsonResponse(serialize_project(project), status=201)


async def project_detail(request, project_id):
    """GET/PUT/PATCH /api/projects/<id>/ - Detail of a project, update name/color, or archive."""
    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Update allowed fields only
        if "name" in data:
            name = data["name"]
            if name is not None:
                name = name.strip()
                if not name:
                    # Empty after strip means no name
                    name = None
                elif len(name) > 25:
                    return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
                elif await Project.objects.filter(name=name).exclude(id=project_id).aexists():
                    return JsonResponse({"error": "A project with this name already exists"}, status=400)
            project.name = name
        if "color" in data:
            project.color = data["color"]
        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            project.archived = archived

        update_fields = ["name", "color", "archived"]
        await run_under_db_write_lock(
            lambda: project.asave(update_fields=update_fields)
        )

        # Broadcast project_updated via WebSocket (out of the DB lock).
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "project_updated",
                    "project": serialize_project(project),
                },
            },
        )

    elif request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            project.archived = archived
            await run_under_db_write_lock(
                lambda: project.asave(update_fields=["archived"])
            )

            # Broadcast project_updated via WebSocket
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )

    return JsonResponse(serialize_project(project))


async def commands(request, project_id):
    """GET /api/projects/<id>/commands/?provider=<key>&activation_char=<char> — commands for a project.

    Returns global commands (``project=NULL``) and project-specific
    commands, sorted by name. Both ``provider`` and ``activation_char``
    query parameters are required: command sets are not interchangeable
    across backends (each provider's CLI has its own command vocabulary),
    and a single provider can expose multiple activation prefixes (e.g.
    Codex uses both ``/`` and ``$``), so there is no implicit default
    for either.
    """
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")

    provider_str = request.GET.get("provider")
    if not provider_str:
        return JsonResponse({"error": "provider is required."}, status=400)
    try:
        provider = Provider(provider_str)
    except ValueError:
        return JsonResponse({"error": f"Unknown provider {provider_str!r}."}, status=400)

    activation_char = request.GET.get("activation_char")
    if not activation_char:
        return JsonResponse({"error": "activation_char is required."}, status=400)
    if len(activation_char) != 1:
        return JsonResponse(
            {"error": "activation_char must be a single character."},
            status=400,
        )

    from django.db.models import Q

    qs = (
        Command.objects
        .filter(provider=provider.value, activation_char=activation_char)
        .filter(Q(project__isnull=True) | Q(project_id=project_id))
        .order_by("name")
        .values("name", "plugin_name", "description", "argument_hint", "is_builtin", "project_id")
    )
    cmds = await sync_to_async(list)(qs)

    return JsonResponse({
        "commands": [
            {
                "name": cmd["name"],
                "plugin_name": cmd["plugin_name"],
                "description": cmd["description"],
                "argument_hint": cmd["argument_hint"],
                "is_builtin": cmd["is_builtin"],
                "is_global": cmd["project_id"] is None,
            }
            for cmd in cmds
        ]
    })


async def user_messages(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/user-messages/ - User messages of a session.

    Returns all user messages for the given session, in chronological order (oldest first).
    Each entry includes line_num, timestamp, and the extracted text content.
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    items = await sync_to_async(list)(
        SessionItem.objects
        .filter(session=session, kind=ItemKind.USER_MESSAGE)
        .order_by("line_num")
    )
    messages = [
        {
            "line_num": msg.line_num,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "text": msg.text,
        }
        for msg in get_provider_helpers(session.provider).get_user_messages(items)
    ]

    return JsonResponse({"messages": messages})


async def project_sessions(request, project_id):
    """GET /api/projects/<id>/sessions/ - Sessions of a project (paginated).

    Returns only regular sessions (not subagents).
    Subagents are accessed via their parent session.

    Query params (optional):
        before_mtime: Cursor for pagination - only return sessions older than this mtime.
    """
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")

    before_mtime = request.GET.get("before_mtime")
    return JsonResponse(await _get_sessions_page(project_id, before_mtime))


async def _resolve_session_or_404(session_id, project_id, parent_session_id):
    """Fetch the session for a session/subagent route, or raise Http404.

    Session route (``parent_session_id`` is None): the session must belong
    to the named project and must not be a subagent -- subagents are only
    reachable through the subagent route.

    Subagent route (``parent_session_id`` is set): the URL is
    ``/projects/<project_id>/sessions/<parent_session_id>/subagent/<session_id>/``.
    Its ``/projects/<project_id>/sessions/<parent_session_id>/`` prefix must
    name a real top-level session of that project -- the same constraint the
    session route enforces -- so ``project_id`` stays meaningful and is not a
    free parameter. The subagent itself is then resolved by its
    globally-unique ``session_id`` and checked to be a child of that parent;
    it is deliberately NOT scoped by ``project_id``, because a Codex subagent
    can run in a different project than its parent.
    """
    if parent_session_id is None:
        try:
            session = await Session.objects.aget(id=session_id, project_id=project_id)
        except Session.DoesNotExist:
            raise Http404("Session not found")
        if session.parent_session_id is not None:
            raise Http404("Session not found")
        return session

    # Subagent route: the parent must be a top-level session of project_id.
    if not await Session.objects.filter(
        id=parent_session_id,
        project_id=project_id,
        parent_session_id__isnull=True,
    ).aexists():
        raise Http404("Session not found")
    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Subagent not found for this parent session")
    if session.parent_session_id != parent_session_id:
        raise Http404("Subagent not found for this parent session")
    return session


async def session_detail(request, project_id, session_id, parent_session_id=None):
    """GET/PATCH /api/projects/<id>/sessions/<session_id>/ - Detail or rename session.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/

    When parent_session_id is provided, validates that session.parent_session_id matches.
    When accessing a subagent via the session endpoint (no parent_session_id in URL), returns 404.

    PATCH: Rename a session (not available for subagents).
        Body: {"title": "New title"}
        - Title is trimmed and must be non-empty
        - Max 200 characters
        - Writes custom-title entry to JSONL file (deferred if process is busy)
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    if request.method == "PATCH":
        # Reject subagents (cannot be modified)
        if session.type == SessionType.SUBAGENT:
            return JsonResponse({"error": "Subagents cannot be modified"}, status=400)

        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Handle title update
        if "title" in data:
            try:
                ensure_provider_running(session.provider)
            except ProviderDisabledError as e:
                return JsonResponse(
                    {"error": "provider_disabled", "provider": e.provider.value, "message": str(e)},
                    status=409,
                )
            provider_helpers = get_provider_helpers(session.provider)
            validation = provider_helpers.validate_title(data["title"])
            if validation.error:
                return JsonResponse({"error": validation.error}, status=400)
            title = validation.title

            # 1. Update DB immediately, under the shared DB write lock.
            session.title = title
            await run_under_db_write_lock(
                lambda: session.asave(update_fields=["title"])
            )

            # 2. Re-index for full-text search (title is a searchable document)
            if search.is_initialized():
                try:
                    await asyncio.to_thread(search.reindex_session, session_id)
                except Exception:
                    pass  # Non-critical: search will catch up on next startup

            # 3. Persist into the provider's session storage (also wires
            #    up any provider-specific anti-stale-write protection).
            try:
                await provider_helpers.rename_session(session_id, title)
            except Exception:
                pass  # Non-critical: DB is already updated, watcher will sync

        # Handle archived update
        needs_broadcast = False
        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            session.archived = archived
            await run_under_db_write_lock(
                lambda: session.asave(update_fields=["archived"])
            )
            needs_broadcast = True

            # Re-index for full-text search (archived flag is denormalized in every document)
            if search.is_initialized():
                try:
                    await asyncio.to_thread(search.reindex_session, session_id)
                except Exception:
                    pass  # Non-critical: search will catch up on next startup

            # Stop process and clean up tmux session if archiving
            if archived:
                await get_agent_manager_registry().kill_agent(session_id, reason="archived")
                await asyncio.to_thread(kill_all_tmux_terminals, f"s:{session_id}")

        # Handle pinned update: NULL (unpinned) or one of PinMode.values.
        if "pinned" in data:
            pinned = data["pinned"]
            if pinned is not None and pinned not in PinMode.values:
                return JsonResponse(
                    {"error": f"pinned must be null or one of {list(PinMode.values)}"},
                    status=400,
                )
            session.pinned = pinned
            await run_under_db_write_lock(
                lambda: session.asave(update_fields=["pinned"])
            )
            needs_broadcast = True

        # Broadcast session_updated for archived/pinned changes.
        # Title changes don't need this: writing to JSONL triggers the
        # file watcher which broadcasts session_updated automatically.
        if needs_broadcast:
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )

    return JsonResponse(serialize_session(session))


async def bulk_archive_sessions(request):
    """POST /api/sessions/bulk-archive/ - Archive multiple sessions in one shot.

    Body:
        older_than (str, required): ISO timestamp. Sessions with mtime < this are eligible.
        scope (str, required): 'project' | 'workspace' | 'all'.
        project_id (str): required if scope == 'project'.
        workspace_id (str): required if scope == 'workspace'.
        dry_run (bool, optional, default False): if True, return only the count.

    Excludes: subagents, already-archived, pinned, sessions with an active agent,
    and sessions without user messages or without created_at (not visible in sidebar).

    Returns: {"count": N}. Session IDs are not in the response — the frontend
    receives them via the `sessions_bulk_archived` WS broadcast.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    older_than_iso = data.get("older_than")
    scope_type = data.get("scope")
    project_id = data.get("project_id")
    workspace_id = data.get("workspace_id")
    dry_run = bool(data.get("dry_run", False))

    if not older_than_iso:
        return JsonResponse({"error": "older_than is required"}, status=400)
    if scope_type not in ("project", "workspace", "all"):
        return JsonResponse({"error": "Invalid scope"}, status=400)
    if scope_type == "project" and not project_id:
        return JsonResponse({"error": "project_id required for scope=project"}, status=400)
    if scope_type == "workspace" and not workspace_id:
        return JsonResponse({"error": "workspace_id required for scope=workspace"}, status=400)

    try:
        older_than_epoch = datetime.fromisoformat(
            older_than_iso.replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, AttributeError, TypeError):
        return JsonResponse({"error": "Invalid older_than format"}, status=400)

    active_ids = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }

    qs = Session.objects.filter(
        type=SessionType.SESSION,
        user_message_count__gt=0,
        created_at__isnull=False,
        archived=False,
        pinned__isnull=True,
        mtime__lt=older_than_epoch,
    ).exclude(id__in=active_ids)

    if scope_type == "project":
        qs = qs.filter(project_id=project_id)
    elif scope_type == "workspace":
        ws_data = await asyncio.to_thread(read_workspaces)
        ws = next(
            (w for w in ws_data.get("workspaces", []) if w["id"] == workspace_id),
            None,
        )
        if ws is None:
            return JsonResponse({"error": "Workspace not found"}, status=404)
        qs = qs.filter(project_id__in=ws.get("projectIds", []))
    # scope_type == "all": no additional filter

    if dry_run:
        return JsonResponse({"count": await qs.acount()})

    # Capture IDs before UPDATE (queryset becomes empty after).
    # Re-check active_ids just before UPDATE to close the TOCTOU window.
    ids = set(await sync_to_async(list)(qs.values_list("id", flat=True)))
    active_ids_now = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }
    ids -= active_ids_now

    # Atomic update under the DB write lock: flip ``archived`` AND reset
    # ``search_version`` to 0 in the same query. The reset guarantees that
    # any session whose Tantivy re-index is interrupted by shutdown (the
    # detached task below) will be picked up by the next-boot search
    # indexing sweep (which selects rows with ``search_version != CURRENT``).
    await run_under_db_write_lock(
        lambda: Session.objects.filter(id__in=ids).aupdate(
            archived=True, search_version=0,
        )
    )

    if ids:
        channel_layer = get_channel_layer()
        await channel_layer.group_send("updates", {
            "type": "broadcast",
            "data": {
                "type": "sessions_bulk_archived",
                "session_ids": list(ids),
            },
        })

        ids_snapshot = list(ids)

        async def post_archive_work():
            for sid in ids_snapshot:
                try:
                    if search.is_initialized():
                        await asyncio.to_thread(search.reindex_session, sid)
                        # Bump ``search_version`` back to CURRENT only once
                        # the Tantivy write has landed — shutdown between
                        # the reindex and this mark leaves the session at
                        # search_version=0 for the next-boot sweep to fix.
                        await run_under_db_write_lock(
                            lambda sid=sid: mark_session_search_version_current(sid)
                        )
                except Exception:
                    logger.exception("bulk archive: reindex failed for session %s", sid)
                try:
                    await asyncio.to_thread(kill_all_tmux_terminals, f"s:{sid}")
                except Exception:
                    logger.exception("bulk archive: tmux cleanup failed for session %s", sid)

        # Strong-reference the task in ``_DETACHED_TASKS`` so the GC can't
        # drop it mid-flight (asyncio only weakly references running tasks).
        # The done callback removes it once finished.
        cleanup_task = asyncio.create_task(post_archive_work(), name="bulk-archive-cleanup")
        _DETACHED_TASKS.add(cleanup_task)
        cleanup_task.add_done_callback(_DETACHED_TASKS.discard)

    return JsonResponse({"count": len(ids)})


async def session_items(request, project_id, session_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/ - Items of a session.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/

    When parent_session_id is provided, validates that session.parent_session_id matches.

    Query params (optional):
        range: one or more ranges (can be repeated)
            Formats:
            - "N" : exact line N
            - "min:max" : lines min to max (inclusive)
            - "min:" : all lines from min onwards
            - ":max" : all lines up to max

    Examples:
        ?range=5                -> line 5 only
        ?range=0:15             -> lines 0 to 15 inclusive
        ?range=10:              -> all lines from 10 onwards
        ?range=:10              -> all lines up to 10
        ?range=0:10&range=20:30&range=50:  -> multiple ranges combined
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    # Filter by line_num ranges (required — refusing to serve the whole session).
    ranges = request.GET.getlist("range")
    if not ranges:
        logger.warning(
            "session_items called without 'range' for session %s (project %s)",
            session_id,
            project_id,
        )
        return JsonResponse(
            {"error": "At least one 'range' query parameter is required"},
            status=400,
        )

    from django.db.models import Q

    q_filter = Q()
    for r in ranges:
        try:
            if ":" not in r:
                # Single number = exact line
                line_val = int(r)
                q_filter |= Q(line_num=line_val)
            else:
                min_str, max_str = r.split(":", 1)
                min_val = int(min_str) if min_str else None
                max_val = int(max_str) if max_str else None

                if min_val is not None and max_val is not None:
                    q_filter |= Q(line_num__gte=min_val, line_num__lte=max_val)
                elif min_val is not None:
                    q_filter |= Q(line_num__gte=min_val)
                elif max_val is not None:
                    q_filter |= Q(line_num__lte=max_val)
                # Both empty = invalid, skip
        except ValueError:
            continue  # Skip invalid ranges

    if not q_filter:
        return JsonResponse(
            {"error": "No valid 'range' query parameter could be parsed"},
            status=400,
        )

    items = await sync_to_async(list)(session.items.filter(q_filter))
    data = [serialize_session_item(item) for item in items]
    return JsonResponse(data, safe=False)


async def session_items_metadata(request, project_id, session_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/metadata/ - Metadata of all items.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/metadata/

    When parent_session_id is provided, validates that session.parent_session_id matches.

    Returns all items with metadata fields but WITHOUT content.
    Used for initial session load to build the visual items list.
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    items = await sync_to_async(list)(
        session.items.all().defer('content')  # Already ordered by line_num (see Meta.ordering)
    )
    data = [serialize_session_item_metadata(item) for item in items]
    return JsonResponse(data, safe=False)


async def tool_results(request, project_id, session_id, line_num, tool_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/<line_num>/tool-results/<tool_id>/

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/<line_num>/tool-results/<tool_id>/

    Returns the tool_result content(s) for a specific tool_use.
    Uses ToolResultLink to find related tool_result items.
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    link_lines = await sync_to_async(list)(
        ToolResultLink.objects.filter(
            session=session,
            tool_use_line_num=line_num,
            tool_use_id=tool_id,
        ).values_list("tool_result_line_num", flat=True)
    )

    if not link_lines:
        return JsonResponse({"results": []})

    items = await sync_to_async(list)(
        SessionItem.objects.filter(
            session=session,
            line_num__in=link_lines,
        ).order_by("line_num")
    )

    results = get_provider_helpers(session.provider).get_tool_results(items, tool_id)
    return JsonResponse({"results": results})


async def subagents_state(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/subagents/

    Returns the agent links for a session: tool_use_id → agent_id mappings.
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    if session.parent_session_id is not None:
        raise Http404("Session not found")

    links = await sync_to_async(list)(
        AgentLink.objects.filter(session=session).order_by("id")
    )
    # Resolve every spawned subagent's slug (Codex's ``agent_nickname``)
    # in a single query so the frontend can label tool cards / tabs
    # without separately hydrating subagent Session rows. ``None`` when
    # the subagent file hasn't been parsed yet (race) or when the
    # provider doesn't carry a slug.
    slugs_by_id = dict(await sync_to_async(list)(
        Session.objects.filter(id__in=[link.agent_id for link in links])
        .values_list("id", "slug")
    ))
    result = [
        {
            "agent_id": link.agent_id,
            "agent_slug": slugs_by_id.get(link.agent_id),
            "tool_use_id": link.tool_use_id,
            "tool_use_line_num": link.tool_use_line_num,
            "is_background": link.is_background,
            "started_at": link.started_at.isoformat() if link.started_at else None,
        }
        for link in links
    ]
    return JsonResponse(result, safe=False)


async def tool_states(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/tool-states/

    Returns the completion state of all tool_use calls in the session:
    result_count, completed_at, error, and optional extra data.

    Response: {"tools": {"toolu_xxx": {"result_count": 2, "completed_at": "...", "error": null, "extra": "..."}, ...}}
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    from django.db.models import Count, Max

    links = await sync_to_async(list)(
        ToolResultLink.objects.filter(session=session)
        .values('tool_use_id')
        .annotate(
            result_count=Count('id'),
            completed_at=Max('tool_result_at'),
            extra=Max('extra'),
            error=Max('error'),
        )
    )

    # Collect every link's tool_result_line_num grouped by tool_use_id
    # so the API exposes the full set rather than just the max — Codex
    # tools can have multiple ToolResultLink rows per call (apply_patch
    # / MCP / web / image: function_call_output + event_msg.*_end;
    # exec_command: the parent's row plus one row per write_stdin polling
    # chunk rebound to the same tool_use_id) at non-adjacent line
    # numbers, and helpers need to walk the chain directly.
    line_nums_by_tool: dict[str, list[int]] = {}
    for tool_use_id, line_num in await sync_to_async(list)(
        ToolResultLink.objects.filter(session=session)
        .order_by('tool_result_line_num')
        .values_list('tool_use_id', 'tool_result_line_num')
    ):
        line_nums_by_tool.setdefault(tool_use_id, []).append(line_num)

    tools = {}
    for entry in links:
        tool_use_id = entry['tool_use_id']
        tools[tool_use_id] = {
            'result_count': entry['result_count'],
            'completed_at': entry['completed_at'].isoformat() if entry['completed_at'] else None,
            'error': entry['error'],
            'extra': entry['extra'],
            'tool_result_line_nums': line_nums_by_tool.get(tool_use_id, []),
        }

    return JsonResponse({"tools": tools})


async def directory_tree(request, project_id, session_id=None):
    """GET directory tree listing.

    Works at project level (/api/projects/<id>/directory-tree/)
    or session level (/api/projects/<id>/sessions/<session_id>/directory-tree/).
    """
    from twicc.file_tree import get_directory_tree, validate_path

    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, request.GET.get("path"), session_id=session_id
    )
    if error:
        return error

    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") == "1"

    tree = await asyncio.to_thread(
        get_directory_tree, dir_path, show_hidden=show_hidden, show_ignored=show_ignored
    )
    return JsonResponse(tree)


async def file_search(request, project_id, session_id=None):
    """GET fuzzy file search.

    Works at project level (/api/projects/<id>/file-search/)
    or session level (/api/projects/<id>/sessions/<session_id>/file-search/).
    """
    from twicc.file_tree import search_files, validate_path

    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, request.GET.get("path"), session_id=session_id
    )
    if error:
        return error

    query = request.GET.get("q", "").strip()
    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") == "1"

    try:
        max_results = int(request.GET.get("limit", 50))
        max_results = max(1, min(max_results, 200))
    except (ValueError, TypeError):
        max_results = 50

    tree = await asyncio.to_thread(
        search_files,
        dir_path, query,
        max_results=max_results,
        show_hidden=show_hidden,
        show_ignored=show_ignored,
    )
    return JsonResponse(tree)


def validate_standalone_root(path, root):
    """Validate that path is within root directory. Returns error response or None."""
    if not root:
        return None  # No restriction
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    if path != root and not path.startswith(root + os.sep):
        return JsonResponse({"error": "Path is outside the allowed root directory"}, status=403)
    return None


async def standalone_directory_tree(request):
    """GET directory tree listing for any absolute directory path.

    Unlike the project-scoped directory-tree endpoint, this does not require
    a project and does not validate path ownership. The user can browse any
    directory accessible with their OS permissions — this is intentional since
    TwiCC is a local-only tool running on the user's own machine.

    Authentication is enforced by PasswordAuthMiddleware.

    Supports an optional ?root= parameter for server-side path restriction.

    If show_ignored is not explicitly passed, defaults to True so directories
    inside git repos are not hidden by .gitignore rules (the user needs to see
    all directories when picking a project root).
    """
    from twicc.file_tree import get_directory_tree

    dir_path = request.GET.get("path", "").strip()
    if not dir_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    if not os.path.isabs(dir_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    if not os.path.isdir(dir_path):
        return JsonResponse({"error": "Directory not found"}, status=404)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(dir_path, root)
    if error:
        return error

    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") != "0" if "show_ignored" in request.GET else True
    directories_only = request.GET.get("directories_only") == "1"

    tree = await asyncio.to_thread(
        get_directory_tree,
        dir_path, show_hidden=show_hidden, show_ignored=show_ignored, directories_only=directories_only,
    )
    return JsonResponse(tree)


async def home_directory(request):
    """GET the current user's home directory path."""
    return JsonResponse({"path": os.path.expanduser("~")})


async def standalone_file_search(request):
    """GET file search for any absolute directory path.

    Unlike the project-scoped file-search endpoint, this does not require
    a project. Supports an optional ?root= parameter for server-side path restriction.

    If show_ignored is not explicitly passed, defaults to True.
    """
    from twicc.file_tree import search_files

    dir_path = request.GET.get("path", "").strip()
    if not dir_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    if not os.path.isabs(dir_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    if not os.path.isdir(dir_path):
        return JsonResponse({"error": "Directory not found"}, status=404)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(dir_path, root)
    if error:
        return error

    query = request.GET.get("q", "").strip()
    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") != "0" if "show_ignored" in request.GET else True

    try:
        max_results = int(request.GET.get("limit", 50))
        max_results = max(1, min(max_results, 200))
    except (ValueError, TypeError):
        max_results = 50

    tree = await asyncio.to_thread(
        search_files,
        dir_path, query,
        max_results=max_results,
        show_hidden=show_hidden,
        show_ignored=show_ignored,
    )
    return JsonResponse(tree)


async def standalone_file_content(request):
    """GET/PUT file content for any absolute file path.

    Unlike the project-scoped file-content endpoint, this does not require
    a project. Supports an optional root parameter for server-side path restriction.
    """
    from twicc.file_content import get_file_content, get_file_meta, write_file_content

    # PUT: write file content
    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        content = data.get("content")
        if content is None:
            return JsonResponse({"error": "Missing 'content' field"}, status=400)

        file_path = (data.get("path") or "").strip()
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)

        file_path = os.path.normpath(file_path)

        if not os.path.isabs(file_path):
            return JsonResponse({"error": "Path must be absolute"}, status=400)

        root = (data.get("root") or "").strip()
        error = validate_standalone_root(file_path, root)
        if error:
            return error

        parent_dir = os.path.dirname(file_path)
        if not os.path.isdir(parent_dir):
            return JsonResponse({"error": "Parent directory not found"}, status=404)

        result = await asyncio.to_thread(write_file_content, file_path, content)
        if result.get("error"):
            return JsonResponse(result, status=400)

        return JsonResponse(result)

    # GET: read file content (default)
    file_path = request.GET.get("path", "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    file_path = os.path.normpath(file_path)

    if not os.path.isabs(file_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(file_path, root)
    if error:
        return error

    if request.GET.get("meta_only"):
        result = await asyncio.to_thread(get_file_meta, file_path)
        if result.get("error"):
            return JsonResponse(result, status=404)
        return JsonResponse(result)

    if not os.path.isfile(file_path):
        return JsonResponse({"error": "File not found"}, status=404)

    result = await asyncio.to_thread(get_file_content, file_path)
    if result.get("error"):
        return JsonResponse(result, status=400)

    return JsonResponse(result)


async def _standalone_file_modify(request, action):
    """Shared logic for standalone file rename, delete, move and create operations."""
    from twicc.file_content import create_path, delete_path, move_path, rename_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    root = (data.get("root") or "").strip()

    if action == "create":
        parent_dir = (data.get("parent_dir") or "").strip()
        if not parent_dir:
            return JsonResponse({"error": "Missing 'parent_dir' field"}, status=400)
        parent_dir = os.path.normpath(parent_dir)
        if not os.path.isabs(parent_dir):
            return JsonResponse({"error": "Path must be absolute"}, status=400)
        error = validate_standalone_root(parent_dir, root)
        if error:
            return error
        name = (data.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Missing 'name' field"}, status=400)
        kind = data.get("kind", "file")
        if kind not in ("file", "directory"):
            return JsonResponse({"error": "Invalid 'kind' field"}, status=400)
        result = await asyncio.to_thread(create_path, parent_dir, name, kind)
    else:
        file_path = (data.get("path") or "").strip()
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)
        file_path = os.path.normpath(file_path)
        if not os.path.isabs(file_path):
            return JsonResponse({"error": "Path must be absolute"}, status=400)
        error = validate_standalone_root(file_path, root)
        if error:
            return error

        if action == "rename":
            new_name = (data.get("new_name") or "").strip()
            if not new_name:
                return JsonResponse({"error": "Missing 'new_name' field"}, status=400)
            result = await asyncio.to_thread(rename_path, file_path, new_name)
        elif action == "move":
            destination_dir = (data.get("destination_dir") or "").strip()
            if not destination_dir:
                return JsonResponse({"error": "Missing 'destination_dir' field"}, status=400)
            destination_dir = os.path.normpath(destination_dir)
            if not os.path.isabs(destination_dir):
                return JsonResponse({"error": "Destination must be absolute"}, status=400)
            error = validate_standalone_root(destination_dir, root)
            if error:
                return error
            result = await asyncio.to_thread(move_path, file_path, destination_dir)
        else:
            result = await asyncio.to_thread(delete_path, file_path)

    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def standalone_file_rename(request):
    """POST: rename a file or directory (standalone)."""
    return await _standalone_file_modify(request, "rename")


async def standalone_file_delete(request):
    """POST: delete a file or directory (standalone)."""
    return await _standalone_file_modify(request, "delete")


async def standalone_file_move(request):
    """POST: move a file or directory (standalone)."""
    return await _standalone_file_modify(request, "move")


async def standalone_file_create(request):
    """POST: create a new file or directory (standalone)."""
    return await _standalone_file_modify(request, "create")


async def file_content(request, project_id, session_id=None):
    """GET/PUT file content.

    Works at project level (/api/projects/<id>/file-content/)
    or session level (/api/projects/<id>/sessions/<session_id>/file-content/).

    GET: read file content (existing behavior).
    PUT: write file content (full replacement).
    """
    from twicc.file_content import get_file_content, get_file_meta, write_file_content
    from twicc.file_tree import validate_path

    # PUT: write file content
    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        content = data.get("content")
        if content is None:
            return JsonResponse({"error": "Missing 'content' field"}, status=400)

        file_path = data.get("path")
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)

        # Validate that the file's directory is within allowed project/session paths
        dir_path = os.path.dirname(os.path.normpath(file_path))
        session, dir_path, error = await sync_to_async(validate_path)(
            project_id, dir_path, session_id=session_id
        )
        if error:
            return error

        normalized = os.path.normpath(file_path)
        result = await asyncio.to_thread(write_file_content, normalized, content)
        if result.get("error"):
            return JsonResponse(result, status=400)

        return JsonResponse(result)

    # GET: read file content (default)
    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    normalized = os.path.normpath(file_path)

    # meta_only: lightweight writable check for files and directories.
    # For directories, validate the path itself (not dirname) since the
    # directory may be an allowed root whose parent is outside scope.
    if request.GET.get("meta_only"):
        check_dir = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
        _session, _check_dir, error = await sync_to_async(validate_path)(project_id, check_dir, session_id=session_id)
        if error:
            return error
        result = await asyncio.to_thread(get_file_meta, normalized)
        if result.get("error"):
            return JsonResponse(result, status=404)
        return JsonResponse(result)

    # Validate that the file's directory is within allowed project/session paths
    dir_path = os.path.dirname(normalized)
    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, dir_path, session_id=session_id
    )
    if error:
        return error

    # Now check the file itself exists
    if not os.path.isfile(normalized):
        return JsonResponse({"error": "File not found"}, status=404)

    result = await asyncio.to_thread(get_file_content, normalized)
    if result.get("error"):
        return JsonResponse(result, status=400)

    return JsonResponse(result)


async def _file_modify(request, project_id, session_id, action):
    """Shared logic for file rename, delete and move operations.

    Create is handled separately in file_create() because it validates the
    parent directory (not the path itself) and takes different parameters.
    """
    from twicc.file_content import delete_path, move_path, rename_path
    from twicc.file_tree import validate_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    file_path = (data.get("path") or "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' field"}, status=400)

    normalized = os.path.normpath(file_path)
    dir_path = os.path.dirname(normalized)
    _session, dir_path, error = await sync_to_async(validate_path)(project_id, dir_path, session_id=session_id)
    if error:
        return error

    if action == "rename":
        new_name = (data.get("new_name") or "").strip()
        if not new_name:
            return JsonResponse({"error": "Missing 'new_name' field"}, status=400)
        result = await asyncio.to_thread(rename_path, normalized, new_name)
    elif action == "move":
        destination_dir = (data.get("destination_dir") or "").strip()
        if not destination_dir:
            return JsonResponse({"error": "Missing 'destination_dir' field"}, status=400)
        dest_normalized = os.path.normpath(destination_dir)
        _session2, _dir_path2, error2 = await sync_to_async(validate_path)(project_id, dest_normalized, session_id=session_id)
        if error2:
            return error2
        result = await asyncio.to_thread(move_path, normalized, dest_normalized)
    else:
        result = await asyncio.to_thread(delete_path, normalized)

    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def file_rename(request, project_id, session_id=None):
    """POST: rename a file or directory."""
    return await _file_modify(request, project_id, session_id, "rename")


async def file_delete(request, project_id, session_id=None):
    """POST: delete a file or directory."""
    return await _file_modify(request, project_id, session_id, "delete")


async def file_move(request, project_id, session_id=None):
    """POST: move a file or directory to a different directory."""
    return await _file_modify(request, project_id, session_id, "move")


async def file_create(request, project_id, session_id=None):
    """POST: create a new file or directory."""
    from twicc.file_content import create_path
    from twicc.file_tree import validate_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    parent_dir = (data.get("parent_dir") or "").strip()
    if not parent_dir:
        return JsonResponse({"error": "Missing 'parent_dir' field"}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Missing 'name' field"}, status=400)

    kind = data.get("kind", "file")
    if kind not in ("file", "directory"):
        return JsonResponse({"error": "Invalid 'kind' field"}, status=400)

    normalized = os.path.normpath(parent_dir)
    _session, _dir_path, error = await sync_to_async(validate_path)(project_id, normalized, session_id=session_id)
    if error:
        return error

    result = await asyncio.to_thread(create_path, normalized, name, kind)
    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def git_log(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-log/

    Returns git commit history for the session's git repository.

    Git directory resolution order:
    1. Session git_directory (from tool_use analysis)
    2. Project git_root (resolved from project directory walking up)

    When session_id is None (project-level, e.g. draft sessions), only the
    project git_root is used.

    The current branch is always resolved dynamically from the git directory
    at request time, ensuring it reflects the actual state.

    Returns 404 if no git context is available.

    Response:
        {
            "current_branch": "main",
            "has_more": true/false,
            "entries": [{ hash, branch, parents, message, committerDate, ... }],
            "index_files": {
                "stats": { "modified": 3, "added": 1, "deleted": 0 },
                "tree": { "name": "", "type": "directory", "loaded": true, "children": [...] }
            }
        }
    """
    from twicc.git import GitError, get_current_branch, get_git_log

    # Optional branch filter from query string
    branch_filter = request.GET.get("branch", "")

    # Resolve git directory using the shared helper.
    # An optional ?git_dir= query param lets the frontend request a specific root.
    requested_git_dir = request.GET.get("git_dir")
    try:
        git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    except Http404:
        return JsonResponse({"error": "No git repository found"}, status=404)

    # Always resolve branch dynamically from the git directory at request time,
    # so the branch selector reflects the actual state (handles worktrees too).
    current_branch = await asyncio.to_thread(get_current_branch, git_directory)

    try:
        result = await asyncio.to_thread(get_git_log, git_directory, branch=branch_filter or None)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    if current_branch:
        result["current_branch"] = current_branch
    return JsonResponse(result)


async def _resolve_session_git_directory(project_id, session_id=None, *, requested_git_dir=None):
    """Resolve the git directory for a session or project.

    Resolution order:
    1. If ``requested_git_dir`` is provided, validate it matches one of the
       known roots (session git_directory or project git_root) and use it.
    2. Session git_directory (from tool_use analysis), if directory still exists
    3. Project git_root (resolved from project directory walking up)

    When session_id is None (project-level, e.g. draft sessions), only the
    project git_root is used.

    Returns the git_directory path or raises Http404.
    """
    session_git = None
    project_git = None

    if session_id:
        try:
            session = await Session.objects.aget(id=session_id, project_id=project_id)
        except Session.DoesNotExist:
            raise Http404("Session not found")

        # Only regular sessions (not subagents)
        if session.parent_session_id is not None:
            raise Http404("Session not found")

        if session.git_directory and os.path.isdir(session.git_directory):
            session_git = session.git_directory

    # Always fetch project (needed for requested_git_dir validation)
    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    if project.git_root and os.path.isdir(project.git_root):
        project_git = project.git_root

    # If a specific directory was requested, validate it against known roots
    if requested_git_dir:
        allowed = {d for d in (session_git, project_git) if d}
        if requested_git_dir in allowed:
            return requested_git_dir
        # Requested directory is not among known/existing roots — reject with 404
        # so the frontend can mark it as missing and fall back to another root.
        raise Http404("No git repository found")

    # Default resolution: session git first, then project git
    if session_git:
        return session_git
    if project_git:
        return project_git

    raise Http404("No git repository found")


async def git_index_files(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-index-files/

    Returns stats and a file tree for uncommitted (index) changes.

    Response:
        {
            "stats": { "modified": 3, "added": 1, "deleted": 0 },
            "tree": { ... }
        }

    Returns ``null`` if there are no uncommitted changes.
    """
    from twicc.git import get_index_files

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_index_files, git_directory)

    return JsonResponse(result, safe=False)


async def git_commit_detail(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-detail/<commit_hash>/

    Returns detailed metadata for a single commit (hash, message, body,
    author, committer, dates).
    """
    from twicc.git import GitError, get_commit_detail

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    try:
        result = await asyncio.to_thread(get_commit_detail, git_directory, commit_hash)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse(result)


async def git_commit_files(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-files/<commit_hash>/

    Returns stats and a file tree for the files changed by a single commit.

    Response:
        {
            "stats": { "modified": 3, "added": 1, "deleted": 0 },
            "tree": {
                "name": "",
                "type": "directory",
                "loaded": true,
                "children": [...]
            }
        }
    """
    from twicc.git import GitError, get_commit_files

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    try:
        result = await asyncio.to_thread(get_commit_files, git_directory, commit_hash)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse(result)


async def git_index_file_diff(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-index-file-diff/

    Returns the original (HEAD) and modified (working tree) content of a file
    for display in the Monaco diff editor.

    Query params:
        path: File path relative to the git root.

    Response:
        {
            "original": "...",   # content at HEAD (null if new file)
            "modified": "...",   # content on disk (null if deleted)
            "binary": false,
            "error": null
        }
    """
    from twicc.git import get_index_file_diff

    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_index_file_diff, git_directory, file_path)

    if result.get("error"):
        return JsonResponse(result, status=500)

    return JsonResponse(result)


async def git_commit_file_diff(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-file-diff/<commit_hash>/

    Returns the original (parent commit) and modified (commit) content of a file
    for display in the Monaco diff editor.

    Query params:
        path: File path relative to the git root.

    Response:
        {
            "original": "...",   # content at parent commit (null if added)
            "modified": "...",   # content at this commit (null if deleted)
            "binary": false,
            "error": null
        }
    """
    from twicc.git import get_commit_file_diff

    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_commit_file_diff, git_directory, commit_hash, file_path)

    if result.get("error"):
        return JsonResponse(result, status=500)

    return JsonResponse(result)


async def _git_file_action(request, project_id, session_id, action):
    """Shared logic for git stage/unstage/discard operations."""
    from twicc.git import GitError, git_discard, git_stage, git_unstage

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    file_path = (data.get("path") or "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' field"}, status=400)

    requested_git_dir = data.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    fn = {"stage": git_stage, "unstage": git_unstage, "discard": git_discard}[action]

    try:
        await asyncio.to_thread(fn, git_directory, file_path)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"ok": True})


async def git_stage_file(request, project_id, session_id=None):
    """POST: stage a file (git add)."""
    return await _git_file_action(request, project_id, session_id, "stage")


async def git_unstage_file(request, project_id, session_id=None):
    """POST: unstage a file (git restore --staged)."""
    return await _git_file_action(request, project_id, session_id, "unstage")


async def git_discard_file(request, project_id, session_id=None):
    """POST: discard unstaged changes (git restore)."""
    return await _git_file_action(request, project_id, session_id, "discard")


_WEEKLY_ACTIVITY_MAX_WEEKS = 52


def _format_weekly_activity(rows, current_monday):
    """Format sparse WeeklyActivity rows into a dense list with zero-filling.

    Args:
        rows: Iterable of dicts with "date" (date object), "user_message_count",
            "session_count" and "cost" keys.
        current_monday: The Monday of the current week.

    Returns:
        List of dicts with "date" (ISO date string), "user_message_count",
        "session_count" and "cost" keys.
        Leading zero-activity weeks are trimmed, with up to 3 padding weeks.
    """
    data_by_week = {
        row["date"]: (row["user_message_count"], row.get("session_count", 0), row.get("cost", 0))
        for row in rows
    }

    start_monday = current_monday - timedelta(weeks=_WEEKLY_ACTIVITY_MAX_WEEKS - 1)

    # Find the first week with data to skip leading zeros
    first_active_monday = None
    for i in range(_WEEKLY_ACTIVITY_MAX_WEEKS):
        monday = start_monday + timedelta(weeks=i)
        user_message_count, session_count, cost = data_by_week.get(monday, (0, 0, 0))
        if user_message_count > 0 or session_count > 0 or cost:
            first_active_monday = monday
            break

    if first_active_monday is None:
        return []

    # Build result from first active week to current week
    result = []
    monday = first_active_monday
    while monday <= current_monday:
        user_message_count, session_count, cost = data_by_week.get(monday, (0, 0, 0))
        result.append({
            "date": monday.isoformat(),
            "user_message_count": user_message_count,
            "session_count": session_count,
            "cost": str(cost) if cost else "0",
        })
        monday += timedelta(weeks=1)

    # Pad with leading zero-activity weeks (up to 3) to approach max weeks
    padding = min(_WEEKLY_ACTIVITY_MAX_WEEKS - len(result), 3)
    for i in range(padding, 0, -1):
        result.insert(0, {
            "date": (first_active_monday - timedelta(weeks=i)).isoformat(),
            "user_message_count": 0,
            "session_count": 0,
            "cost": "0",
        })

    return result


async def home_data(request):
    """GET /api/home/ - Home page data: projects with weekly activity.

    Activity is summed across every provider — the home page shows a
    high-level overview, so per-provider filtering would not be
    meaningful here. The per-project detail pages use
    ``/api/daily-activity/`` instead, which exposes a ``provider``
    query param for that purpose.

    Returns:
        {
            "projects": [ { ...project..., "weekly_activity": [...] }, ... ],
            "global_weekly_activity": [ { "date": "...", "user_message_count": N, "session_count": N }, ... ]
        }
    """
    from django.db.models import Sum

    today = timezone.now().date()
    current_monday = today - timedelta(days=today.weekday())
    cutoff = current_monday - timedelta(weeks=_WEEKLY_ACTIVITY_MAX_WEEKS - 1)

    projects = await sync_to_async(list)(Project.objects.all())

    # Load all weekly activities in a single query (within the 52-week window).
    # Always aggregate per (project, date) so multi-provider rows collapse
    # into one entry per project/date.
    all_activities = await sync_to_async(list)(
        WeeklyActivity.objects
        .filter(date__gte=cutoff)
        .values("project_id", "date")
        .annotate(
            user_message_count=Sum("user_message_count"),
            session_count=Sum("session_count"),
            cost=Sum("cost"),
        )
    )

    # Group by project_id (None = global)
    from collections import defaultdict

    activities_by_project = defaultdict(list)
    for a in all_activities:
        activities_by_project[a["project_id"]].append(a)

    data = []
    for p in projects:
        d = serialize_project(p)
        d["weekly_activity"] = _format_weekly_activity(
            activities_by_project.get(p.id, []), current_monday
        )
        data.append(d)

    global_activity = _format_weekly_activity(
        activities_by_project.get(None, []), current_monday
    )

    return JsonResponse({
        "projects": data,
        "global_weekly_activity": global_activity,
    })


_DAILY_ACTIVITY_MAX_DAYS = 365


async def daily_activity(request, project_id=None):
    """GET /api/daily-activity/ or /api/projects/<id>/daily-activity/

    Returns daily activity data for the contribution graph, plus all-time totals.
    Sparse format: only days with activity are returned.
    The frontend heatmap component handles missing days as empty cells.

    If project_id is provided, returns per-project data.
    If project_id is omitted, returns global data (all projects).

    Query params (optional, only for /api/daily-activity/):
        project_ids: Comma-separated list of project IDs to filter by (e.g. workspace projects).
                     When provided, aggregates activity across the specified projects.
        provider: Optional. Backend provider key (e.g. ``claude_code``) to
                  scope the activity to a single provider. When omitted,
                  counts and costs are summed across every provider.

    Returns:
        {
            "daily_activity": [ { "date": "YYYY-MM-DD", "user_message_count": N, "session_count": N, "cost": "X.XX" }, ... ],
            "totals": { "user_message_count": N, "session_count": N, "cost": "X.XX" }
        }
    """
    from django.db.models import Sum

    provider_str = request.GET.get("provider")
    if provider_str:
        try:
            provider_filter = {"provider": Provider(provider_str).value}
        except ValueError:
            return JsonResponse({"error": f"Unknown provider: {provider_str!r}."}, status=400)
    else:
        provider_filter = {}

    today = timezone.now().date()
    cutoff = today - timedelta(days=_DAILY_ACTIVITY_MAX_DAYS - 1)

    # Determine filtering: specific project, set of projects (workspace), or global
    project_ids_param = request.GET.get("project_ids") if not project_id else None
    if project_id:
        project_filters = {"project_id": project_id}
    elif project_ids_param:
        project_filters = {"project_id__in": project_ids_param.split(",")}
    else:
        project_filters = {"project__isnull": True}

    # Always aggregate per date so multi-provider rows collapse into one
    # entry per day. With ``?provider=`` the sum runs over a single row
    # (still correct), or a single project's row in the per-project case.
    qs = DailyActivity.objects.filter(date__gte=cutoff, **project_filters, **provider_filter)
    rows = await sync_to_async(list)(
        qs.values("date").annotate(
            user_message_count=Sum("user_message_count"),
            session_count=Sum("session_count"),
            cost=Sum("cost"),
        ).order_by("date")
    )

    # All-time totals (no date filter)
    totals = await DailyActivity.objects.filter(**project_filters, **provider_filter).aaggregate(
        total_user_message_count=Sum("user_message_count"),
        total_session_count=Sum("session_count"),
        total_cost=Sum("cost"),
    )

    return JsonResponse({
        "daily_activity": [
            {
                "date": row["date"].isoformat(),
                "user_message_count": row["user_message_count"],
                "session_count": row["session_count"],
                "cost": str(row["cost"]) if row["cost"] else "0",
            }
            for row in rows
        ],
        "totals": {
            "user_message_count": totals["total_user_message_count"] or 0,
            "session_count": totals["total_session_count"] or 0,
            "cost": str(totals["total_cost"] or 0),
        },
    })


async def search_sessions(request):
    """GET /api/search/ - Full-text search across session messages."""
    if not search.is_initialized():
        return JsonResponse({"error": "Search index not ready"}, status=503)

    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"error": "Missing required parameter: q"}, status=400)

    project_id = request.GET.get("project_id")
    project_ids = request.GET.getlist("project_ids")
    session_id = request.GET.get("session_id")

    from_role = request.GET.get("from")
    if from_role is not None and from_role not in ("user", "assistant", "title"):
        return JsonResponse({"error": "Invalid 'from' parameter: must be 'user', 'assistant', or 'title'"}, status=400)

    after = None
    before = None
    try:
        if raw_after := request.GET.get("after"):
            after = datetime.fromisoformat(raw_after)
    except ValueError:
        return JsonResponse({"error": f"Invalid 'after' parameter: {raw_after!r} is not a valid ISO datetime"}, status=400)
    try:
        if raw_before := request.GET.get("before"):
            before = datetime.fromisoformat(raw_before)
    except ValueError:
        return JsonResponse({"error": f"Invalid 'before' parameter: {raw_before!r} is not a valid ISO datetime"}, status=400)

    include_archived = request.GET.get("include_archived", "").lower() in ("true", "1", "yes")

    try:
        limit = min(int(request.GET.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20
    try:
        offset = int(request.GET.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0

    try:
        results = await asyncio.to_thread(
            search.search,
            q,
            project_id=project_id,
            project_ids=project_ids or None,
            session_id=session_id,
            from_role=from_role,
            after=after,
            before=before,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return JsonResponse({"error": f"Search failed: {exc}"}, status=500)

    # Enrich results with session titles and project names from DB
    session_ids = [sr.session_id for sr in results.results]
    sessions_info = {}
    if session_ids:
        enriched_sessions = await sync_to_async(list)(
            Session.objects.filter(id__in=session_ids).select_related("project")
        )
        for s in enriched_sessions:
            sessions_info[s.id] = {
                "title": s.title or "",
                "project_id": s.project_id or "",
                "project_name": s.project.name if s.project else "",
                "archived": s.archived,
            }

    return JsonResponse({
        "query": q,
        "total_sessions": results.total_sessions,
        "results": [
            {
                "session_id": sr.session_id,
                "session_title": sessions_info.get(sr.session_id, {}).get("title", ""),
                "project_id": sessions_info.get(sr.session_id, {}).get("project_id", ""),
                "project_name": sessions_info.get(sr.session_id, {}).get("project_name", ""),
                "archived": sessions_info.get(sr.session_id, {}).get("archived", False),
                "score": sr.score,
                "matches": [
                    {
                        "line_num": m.line_num,
                        "from": m.from_role,
                        "snippet": m.snippet,
                        "score": m.score,
                        "timestamp": m.timestamp,
                    }
                    for m in sr.matches
                ],
            }
            for sr in results.results
        ],
    })


async def usage_history(request):
    """GET /api/usage-history/?provider=<key>&range_days=30&bucket_minutes=60&before=...

    Returns historical usage snapshots for charting utilization and burn rate over time.
    Both five_hour and seven_day data are returned in a single response.

    Parameters:
        provider: Required. Backend provider key (e.g. ``claude_code``). 400 when
            missing or unknown — there is no implicit default since multiple
            providers can track usage independently.
        range_days: Number of days to look back (default 30, 0.25–1825).
        bucket_minutes: Aggregation bucket size in minutes (default 0 = raw data).
            Allowed: 0, 30, 60, 300, 720, 1440.
            When > 0, snapshots are grouped into time buckets and the max value
            of each metric is kept per bucket.
        before: Optional ISO datetime. When present, the range ends at this time
            instead of now. Used for panning the usage history chart into the past.
    """
    ALLOWED_BUCKETS = {0, 30, 60, 300, 720, 1440}

    provider_str = request.GET.get("provider")
    if not provider_str:
        return JsonResponse({"error": "provider is required."}, status=400)
    try:
        provider = Provider(provider_str)
    except ValueError:
        return JsonResponse({"error": f"Unknown provider: {provider_str!r}."}, status=400)

    try:
        range_days = float(request.GET.get("range_days", "30"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "range_days must be a number."}, status=400)
    if range_days < 0.25 or range_days > 1825:
        return JsonResponse({"error": "range_days must be between 0.25 and 1825."}, status=400)

    try:
        bucket_minutes = int(request.GET.get("bucket_minutes", "0"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "bucket_minutes must be an integer."}, status=400)
    if bucket_minutes not in ALLOWED_BUCKETS:
        return JsonResponse({"error": f"bucket_minutes must be one of {sorted(ALLOWED_BUCKETS)}."}, status=400)

    before_str = request.GET.get("before")
    if before_str:
        try:
            end = datetime.fromisoformat(before_str)
            if end.tzinfo is None:
                end = timezone.make_aware(end)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid 'before' datetime format."}, status=400)
    else:
        end = timezone.now()

    cutoff = end - timedelta(days=range_days)

    snapshots = await sync_to_async(list)(
        UsageSnapshot.objects
        .filter(provider=provider.value)
        .filter(fetched_at__gte=cutoff, fetched_at__lte=end)
        .order_by("fetched_at")
    )

    FIVE_HOURS_S = 5 * 3600
    ONE_HOUR_S = 3600
    THIRTY_MIN_S = 1800
    SEVEN_DAYS_S = 7 * 86400
    ONE_DAY_S = 86400
    TWELVE_HOURS_S = 12 * 3600

    # Recent burn rate: compares current utilization with a reference snapshot
    # from ~lookback_seconds ago, measuring consumption rate over that recent interval.
    # Mirrors the frontend recentBurnRate() in utils/usage.js.
    # Formula: (delta_utilization) / ((delta_time / window) * 100)
    # When a reset boundary is crossed (delta_util < 0), computes a cross-period
    # rate by summing consumption from the old and new periods.
    def _compute_recent_rates(snaps, epochs, lookback_seconds, window_seconds, util_field, resets_at_field):
        n = len(snaps)
        rates = [None] * n
        for i in range(n):
            target = epochs[i] - lookback_seconds
            # Binary search for closest snapshot to target, only looking before i
            pos = bisect_left(epochs, target, 0, i)
            best_idx = None
            best_dist = float("inf")
            for candidate in (pos - 1, pos):
                if 0 <= candidate < i:
                    dist = abs(epochs[candidate] - target)
                    if dist < best_dist:
                        best_idx = candidate
                        best_dist = dist
            if best_idx is None or best_dist > lookback_seconds:
                continue
            current_util = getattr(snaps[i], util_field)
            ref_util = getattr(snaps[best_idx], util_field)
            if current_util is None or ref_util is None:
                continue
            delta_util = current_util - ref_util
            delta_seconds = epochs[i] - epochs[best_idx]
            if delta_seconds <= 0:
                continue
            delta_time_pct = (delta_seconds / window_seconds) * 100
            if delta_time_pct <= 0:
                continue

            if delta_util >= 0:
                # Normal intra-period
                rates[i] = delta_util / delta_time_pct
            else:
                # Cross-period: a reset happened between ref and current.
                # Find prev_end (last snapshot before the current window started)
                # and sum old-period + new-period consumption.
                resets_at = getattr(snaps[i], resets_at_field)
                if resets_at is None:
                    continue
                window_start_epoch = (resets_at - timedelta(seconds=window_seconds)).timestamp()
                # Last snapshot before the period boundary
                boundary_pos = bisect_left(epochs, window_start_epoch, best_idx, i)
                prev_end_idx = boundary_pos - 1
                if prev_end_idx < best_idx:
                    continue
                prev_end_util = getattr(snaps[prev_end_idx], util_field)
                if prev_end_util is None:
                    continue
                old_consumption = prev_end_util - ref_util
                if old_consumption < 0:
                    continue  # another reset between ref and prev_end
                total_consumption = old_consumption + current_util
                rates[i] = total_consumption / delta_time_pct
        return rates

    # Precompute epoch timestamps and recent rates for all 4 intervals
    epochs = [s.fetched_at.timestamp() for s in snapshots]
    fh_recent_long_rates = _compute_recent_rates(snapshots, epochs, ONE_HOUR_S, FIVE_HOURS_S, "five_hour_utilization", "five_hour_resets_at")
    fh_recent_short_rates = _compute_recent_rates(snapshots, epochs, THIRTY_MIN_S, FIVE_HOURS_S, "five_hour_utilization", "five_hour_resets_at")
    sd_recent_long_rates = _compute_recent_rates(snapshots, epochs, ONE_DAY_S, SEVEN_DAYS_S, "seven_day_utilization", "seven_day_resets_at")
    sd_recent_short_rates = _compute_recent_rates(snapshots, epochs, TWELVE_HOURS_S, SEVEN_DAYS_S, "seven_day_utilization", "seven_day_resets_at")

    # Extract raw data points with both periods
    raw = []
    for i, s in enumerate(snapshots):
        fh_burn = s.five_hour_burn_rate
        sd_burn = s.seven_day_burn_rate
        fh_rl = fh_recent_long_rates[i]
        fh_rs = fh_recent_short_rates[i]
        sd_rl = sd_recent_long_rates[i]
        sd_rs = sd_recent_short_rates[i]
        raw.append({
            "fetched_at": s.fetched_at,
            "fh_utilization": s.five_hour_utilization,
            "fh_burn_rate": round(fh_burn * 100, 1) if fh_burn is not None else None,
            "fh_recent_long": round(fh_rl * 100, 1) if fh_rl is not None else None,
            "fh_recent_short": round(fh_rs * 100, 1) if fh_rs is not None else None,
            "fh_temporal_pct": round(s.five_hour_temporal_pct, 1) if s.five_hour_temporal_pct is not None else None,
            "sd_utilization": s.seven_day_utilization,
            "sd_burn_rate": round(sd_burn * 100, 1) if sd_burn is not None else None,
            "sd_recent_long": round(sd_rl * 100, 1) if sd_rl is not None else None,
            "sd_recent_short": round(sd_rs * 100, 1) if sd_rs is not None else None,
            "sd_temporal_pct": round(s.seven_day_temporal_pct, 1) if s.seven_day_temporal_pct is not None else None,
        })

    # All metric keys (used for bucket aggregation and serialization)
    _METRIC_KEYS = (
        "fh_utilization", "fh_burn_rate",
        "fh_recent_long", "fh_recent_short", "fh_temporal_pct",
        "sd_utilization", "sd_burn_rate",
        "sd_recent_long", "sd_recent_short", "sd_temporal_pct",
    )

    # Aggregate into buckets if requested
    if bucket_minutes > 0 and raw:
        bucket_seconds = bucket_minutes * 60
        buckets = {}  # bucket_start_epoch -> aggregated values
        for point in raw:
            epoch = point["fetched_at"].timestamp()
            bucket_key = int(epoch // bucket_seconds) * bucket_seconds
            if bucket_key not in buckets:
                buckets[bucket_key] = {"epoch": bucket_key, **{k: point[k] for k in _METRIC_KEYS}}
            else:
                b = buckets[bucket_key]
                # Keep max of each metric (treating None as absent)
                for key in _METRIC_KEYS:
                    old_val = b[key]
                    new_val = point[key]
                    if new_val is not None:
                        b[key] = max(old_val, new_val) if old_val is not None else new_val

        # Convert back to sorted list with datetime
        aggregated = []
        for bucket_key in sorted(buckets):
            b = buckets[bucket_key]
            entry = {"fetched_at": datetime.fromtimestamp(b["epoch"], tz=timezone.get_current_timezone())}
            entry.update({k: b[k] for k in _METRIC_KEYS})
            aggregated.append(entry)
        raw = aggregated

    # Serialize
    data = []
    for point in raw:
        entry = {"fetched_at": point["fetched_at"].isoformat()}
        entry.update({k: point[k] for k in _METRIC_KEYS})
        data.append(entry)

    return JsonResponse({"snapshots": data})


async def bootstrap(request):
    """GET /api/bootstrap/ - All data needed before the app can mount.

    Returns synced settings (with defaults and categories), workspaces,
    terminal config, and message snippets in a single response so the
    frontend doesn't have to wait for the WebSocket connection.
    """
    from twicc.message_snippets import read_message_snippets_config
    from twicc.seen_tips import read_seen_tips
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS, prepare_settings_for_client, read_synced_settings
    from twicc.terminal_config import read_terminal_config
    from twicc.tips_manifest import manifest_to_dict
    from twicc.workspaces import read_workspaces

    # Bootstrap reads several user config / settings JSON files plus
    # workspace state — none of it is hot, but file I/O on the event loop
    # would still block ASGI. Hop into a worker thread for the lot.
    raw_settings = await asyncio.to_thread(read_synced_settings)
    clean_settings, version = prepare_settings_for_client(raw_settings)
    disabled_providers_present = "disabledProviders" in raw_settings
    disabled_providers = (raw_settings.get("disabledProviders") or []) if disabled_providers_present else []
    from twicc.providers.state import get_all_provider_states
    provider_states = get_all_provider_states()
    workspaces_data = await asyncio.to_thread(read_workspaces)
    terminal_config = await asyncio.to_thread(read_terminal_config)
    message_snippets = await asyncio.to_thread(read_message_snippets_config)
    seen_tips = await asyncio.to_thread(read_seen_tips)
    tips_manifest = await asyncio.to_thread(manifest_to_dict)
    # ``helpers.get_bootstrap_data()`` does sync FS reads and sync ORM
    # work per provider — build the whole map in one worker thread hop.
    providers_data = await asyncio.to_thread(
        lambda: {
            provider.value: helpers.get_bootstrap_data()
            for provider, helpers in get_provider_helpers_registry().items()
        }
    )
    return JsonResponse({
        "settings": clean_settings,
        "settings_version": version,
        "default_settings": SYNCED_SETTINGS_DEFAULTS,
        "dev_mode": settings.DEV_MODE,
        "uvx_mode": settings.UVX_MODE,
        "twicc_launch_prefix": settings.TWICC_LAUNCH_PREFIX,
        "workspaces": workspaces_data.get("workspaces", []),
        "terminal_config": terminal_config,
        "message_snippets": message_snippets,
        "seen_tips": seen_tips,
        "tips_manifest": tips_manifest,
        "providers": providers_data,
        "disabledProvidersPresent": disabled_providers_present,
        "disabledProviders": disabled_providers,
        "providerStates": provider_states,
    })


async def changelog(request):
    """GET /api/changelog/ - Serve the local CHANGELOG.md (dev mode only)."""
    if not settings.DEV_MODE:
        raise Http404
    changelog_path = settings.PACKAGE_DIR.parent.parent / "CHANGELOG.md"
    if not changelog_path.is_file():
        raise Http404
    return HttpResponse(changelog_path.read_bytes(), content_type="text/plain; charset=utf-8")


async def spa_index(request):
    """Catch-all for Vue Router - serves index.html."""
    index_path = settings.FRONTEND_DIST_DIR / "index.html"
    if not index_path.exists():
        raise Http404("Frontend not built. Run 'npm run build' in frontend/")
    return HttpResponse(index_path.read_bytes(), content_type="text/html")
