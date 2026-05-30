"""Create a new agent session from a generic payload.

Called by both ``WSConsumer._handle_send_message`` (when the front-end sends
``send_message``) and ``PendingSessionsWatcher`` (when the CLI drops a
request file). Centralises validation, project resolution, pending-settings
stashing, and agent-manager invocation so both entry points stay in sync.

The function does NOT raise for business-rule errors (missing project,
disabled provider, etc.); it returns a :class:`SessionCreationResult` with
``success=False`` and a list of structured error dicts. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

from typing import NamedTuple

from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.pending_agent_settings import set_pending_agent_settings
from twicc.pending_session_attributes import set_pending_session_attributes
from twicc.pending_titles import set_pending_title
from twicc.projects import register_project
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


class SessionCreationError(NamedTuple):
    field: str
    code: str
    message: str


class SessionCreationResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionCreationError] | None


async def create_session_from_payload(payload: dict) -> SessionCreationResult:
    """Create a new session from a normalised payload.

    Expected keys in ``payload``:
    - ``session_id``: client-supplied UUID (used as Claude Code session id;
      Codex mints its own and the canonical id is returned).
    - ``project_id``: project identifier (must exist in DB unless
      ``directory`` is also provided — see next entry).
    - ``directory``: optional. When present (CLI flow), the server creates
      the Project here via :func:`twicc.projects.register_project` if it
      doesn't exist yet. This is what makes ``project_added`` and
      ``workspaces_updated`` broadcasts originate from the main process,
      where they can reach connected UI clients live (the CLI runs in a
      separate process and can't broadcast itself). UI flow omits this
      key — the Project is then required to exist already.
    - ``provider``: string value of ``Provider`` enum.
    - ``text``: non-empty for new sessions.
    - ``title``: optional, max 200 chars.
    - ``images``, ``documents``: lists of SDK block dicts (already validated
      by the caller — the service does not re-validate attachments).
    - Plus all six ``AgentSettings`` fields (``None`` = use synced default).
    """
    # --- payload extraction (defensive, no schema validation) ----
    session_id = payload.get("session_id")
    project_id = payload.get("project_id")
    directory_hint = payload.get("directory")
    provider_str = payload.get("provider")
    text = (payload.get("text") or "").strip()
    title = payload.get("title")
    images = payload.get("images") or []
    documents = payload.get("documents") or []
    hidden = bool(payload.get("hidden", False))
    spawned_by_session_id = payload.get("spawned_by_session_id")  # str | None

    errors: list[SessionCreationError] = []
    if not session_id:
        errors.append(SessionCreationError("session_id", "missing", "session_id is required"))
    if not project_id:
        errors.append(SessionCreationError("project_id", "missing", "project_id is required"))
    if not provider_str:
        errors.append(SessionCreationError("provider", "missing", "provider is required"))
    if not text:
        errors.append(SessionCreationError("text", "empty_text", "text is required for a new session"))
    if errors:
        return SessionCreationResult(False, None, None, None, errors)

    # --- provider resolution ---------------------------------------
    try:
        provider = Provider(provider_str)
    except ValueError:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "unknown_provider", f"Unknown provider: {provider_str}")
        ])

    # --- runtime gate ----------------------------------------------
    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "provider_disabled", str(e))
        ])

    # --- project resolution / creation ---------------------------
    # CLI flow (``directory`` provided): get-or-create the Project here so
    # ``project_added`` + workspace auto-add broadcasts fire from the main
    # process. ``register_project`` also adopts the directory if the row
    # existed but had none yet (typical of claude_code/initial_sync rows
    # before background compute filled in the cwd).
    #
    # UI flow (no ``directory`` in payload): the Project is expected to
    # exist in DB already (created via ``POST /api/projects/`` or detected
    # by the sessions watcher).
    from twicc.core.models import Project
    if directory_hint:
        # ``register_project`` is the only direct DB write in this
        # service before the manager handoff — everything else here is
        # reads or in-memory caches. (``manager.create_session`` below
        # does perform DB writes itself, e.g. ``ProcessRun`` creation
        # and session-row updates inside ``BaseAgentManager._register_
        # and_start``; those will be wired separately in WIRE#3.) Wrap
        # it under
        # the DB write lock so the project create/adopt + the
        # ``project_added`` / workspace-auto-add broadcasts that follow
        # serialise FIFO with every other DB writer (watcher, DB-writer
        # consumer, etc.). The lock is NOT held across
        # ``manager.create_session`` below — that call boots the SDK
        # and can take seconds; holding the lock through it would
        # stall every other writer. Reentrance (LOCK#2) makes this
        # safe even if a future caller of the service wraps it under
        # the lock too.
        project, _ = await run_under_db_write_lock(
            lambda pid=project_id, d=directory_hint: register_project(pid, directory=d)
        )
    else:
        try:
            project = await sync_to_async(Project.objects.get)(id=project_id)
        except Project.DoesNotExist:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError("project_id", "project_not_found",
                                      f"Project {project_id!r} not found")
            ])

    cwd = project.directory
    if not cwd:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("project_id", "project_no_directory",
                                  f"Project {project_id!r} has no directory set")
        ])

    # --- spawned_by validation -----------------------------------
    # A forged or stale ID would later raise IntegrityError on the FK
    # constraint when the watcher creates the Session row. Fail loudly
    # now with a clear error rather than a downstream stack trace.
    if spawned_by_session_id is not None:
        from twicc.core.models import Session
        exists = await sync_to_async(
            lambda: Session.objects.filter(pk=spawned_by_session_id).exists()
        )()
        if not exists:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError(
                    "spawned_by_session_id", "invalid_spawned_by",
                    f"spawned_by session {spawned_by_session_id!r} does not exist",
                )
            ])

    # --- build agent settings from the closed bundle --------------
    # NOTE: this reads every AgentSettings field — including those listed in
    # ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` — because this service is also
    # the CLI drop-file entry point, and the CLI is a legitimate backend-side
    # source that can set hidden fields (e.g. ``--no-question-widget``). The
    # WS path strips hidden fields upstream via
    # ``agent_settings_kwargs_from_frontend_payload`` before calling here.
    agent_settings = AgentSettings(**{
        field: payload.get(field) for field in AgentSettings._fields
    })

    # --- resolve provider helpers (needed for title validation and
    #     agent-settings resolution below) -------------------------
    helpers = get_provider_helpers(provider)

    # --- title --------------------------------------------------------
    if title is not None:
        title_result = helpers.validate_title(title)
        if title_result.error:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError("title", "invalid_title", title_result.error)
            ])
        set_pending_title(session_id, title_result.title)

    # --- stash agent settings (consumed by the watcher when it creates
    #     the Session row from the JSONL) ---------------------------
    set_pending_agent_settings(session_id, agent_settings)
    set_pending_session_attributes(
        session_id,
        hidden=hidden,
        spawned_by_id=spawned_by_session_id,
    )

    # --- resolve to effective settings (None -> synced default) --
    effective = helpers.resolve_agent_settings(agent_settings)
    # enforce_agent_settings_consistency RETURNS an AgentSettings (may be
    # the same instance if no demotion was needed, or a fresh one via
    # _replace). Capture it.
    effective = helpers.enforce_agent_settings_consistency(effective)

    # --- hidden constraints (defence in depth) -------------------
    # The CLI validates these before writing the drop-file; we re-validate
    # from the payload because the drop-file is a trust boundary (forged
    # or version-skewed callers can submit invalid combinations).
    from twicc.cli._session_request.validation import validate_hidden_constraints
    hidden_errors = validate_hidden_constraints(
        provider.value, effective, hidden=hidden,
    )
    if hidden_errors:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError(e.field, e.code, e.message) for e in hidden_errors
        ])

    # --- invoke the agent manager --------------------------------
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    try:
        canonical_id = await manager.create_session(
            session_id, project_id, cwd, text,
            settings=effective, images=images, documents=documents,
        )
    except RuntimeError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("session", "manager_busy", str(e))
        ])

    return SessionCreationResult(
        success=True,
        session_id=canonical_id,
        provider=provider.value,
        project_id=project_id,
        errors=None,
    )
