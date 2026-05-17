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
from twicc.pending_titles import set_pending_title
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
    - ``project_id``: must exist in DB with ``directory`` set.
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
    provider_str = payload.get("provider")
    text = (payload.get("text") or "").strip()
    title = payload.get("title")
    images = payload.get("images") or []
    documents = payload.get("documents") or []

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

    # --- project directory ----------------------------------------
    from twicc.core.models import Project
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

    # --- build agent settings from the closed bundle --------------
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

    # --- resolve to effective settings (None -> synced default) --
    effective = helpers.resolve_agent_settings(agent_settings)
    # enforce_agent_settings_consistency RETURNS an AgentSettings (may be
    # the same instance if no demotion was needed, or a fresh one via
    # _replace). Capture it.
    effective = helpers.enforce_agent_settings_consistency(effective)

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
