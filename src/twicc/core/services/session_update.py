"""Apply a settings update to an existing agent session.

Called by :class:`PendingSessionsWatcher` when the CLI drops a request file
with ``kind="update_settings"``. Mirrors the settings-only branch of
:meth:`twicc.asgi.WSConsumer._handle_send_message`: write the new values to
the ``Session`` row under the DB write lock, broadcast ``session_updated``
out of the lock, and propagate to the live agent via
``manager.send_to_session(text="")`` when a process is attached.

The function does NOT raise for business-rule errors (missing session,
provider disabled, etc.); it returns an :class:`UpdateSessionResult` with
``success=False`` and a list of structured error tuples. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.enums import Provider
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


logger = logging.getLogger(__name__)


class UpdateSessionError(NamedTuple):
    field: str
    code: str
    message: str


class UpdateSessionResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[UpdateSessionError] | None


async def update_session_settings_from_payload(payload: dict) -> UpdateSessionResult:
    """Apply a partial or full settings update to an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the target session (required).
    - ``updates``: dict mapping AgentSettings field name → new value. A key
      present in the dict means "write this value" (including ``None``,
      which resets to "use the synced default"). A key absent from the
      dict means "do not touch this field".
    - ``replace_all``: bool — informational only (telemetry / log). True
      means the CLI assembled the dict from a preset replacement; False
      means a patch from individual flags / ``--unset``.

    Business-rule rejections (returned as ``success=False``):
    - ``empty_updates``: ``updates`` is missing or empty.
    - ``invalid_field``: ``updates`` contains a key that is not an
      AgentSettings field.
    - ``session_not_found``: no row in DB for that id.
    - ``is_subagent``: the row exists but is a subagent. Subagents cannot
      be updated directly; the parent session is the right target.
    - ``session_stale``: ``Session.stale=True`` (file gone from disk).
    - ``project_no_directory``: the owning project has no directory set.
    - ``unknown_provider``: ``Session.provider`` does not match the enum.
    - ``provider_disabled``: the owning provider was disabled in settings.
    """
    session_id = payload.get("session_id")
    updates: dict[str, Any] = payload.get("updates") or {}
    replace_all = bool(payload.get("replace_all"))

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if not updates:
        errors.append(UpdateSessionError("updates", "empty_updates",
                                          "updates dict is empty; nothing to write"))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    invalid_keys = [k for k in updates if k not in AgentSettings._fields]
    if invalid_keys:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError(
                "updates", "invalid_field",
                f"Unknown AgentSettings field(s): {sorted(invalid_keys)}",
            ),
        ])

    # --- session lookup -------------------------------------------------
    from twicc.core.models import Session, SessionType
    from twicc.core.serializers import serialize_session

    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_not_found",
                                f"Session {session_id!r} not found"),
        ])
    if session.type == SessionType.SUBAGENT:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "is_subagent",
                                f"Session {session_id!r} is a subagent; "
                                "subagents cannot be updated directly. "
                                "Target the parent session instead."),
        ])
    if session.stale:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_stale",
                                f"Session {session_id!r} is stale "
                                "(its JSONL file no longer exists on disk)"),
        ])

    project = session.project
    if project is None or not project.directory:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "project_no_directory",
                                f"Session {session_id!r} has no project directory"),
        ])

    # --- provider resolution --------------------------------------------
    try:
        provider = Provider(session.provider)
    except ValueError:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "unknown_provider",
                                f"Session {session_id!r} has unknown provider "
                                f"{session.provider!r}"),
        ])

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("provider", "provider_disabled", str(e)),
        ])

    # --- DB write under the write lock ---------------------------------
    # Mirrors the path taken by ``WSConsumer._handle_send_message`` for the
    # settings-only update: aupdate under the lock; reload + broadcast
    # outside the lock so other writers don't queue behind the broadcast.
    await run_under_db_write_lock(
        lambda: Session.objects.filter(id=session_id).aupdate(**updates)
    )

    updated_session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()

    channel_layer = get_channel_layer()
    if updated_session is not None and channel_layer is not None:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(updated_session),
                },
            },
        )

    logger.info(
        "[update_session_settings] session=%s replace_all=%s fields=%s",
        session_id, replace_all, sorted(updates.keys()),
    )

    # --- propagate to the live agent if one is attached ----------------
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    if manager.get_agent_info(session_id) is None:
        # No live agent: the DB is the source of truth and the next session
        # resume will pick up the new settings naturally.
        return UpdateSessionResult(
            success=True,
            session_id=session_id,
            provider=provider.value,
            project_id=session.project_id,
            errors=None,
        )

    helpers = get_provider_helpers(provider)
    # Rehydrate from the freshly reloaded row so the manager sees exactly
    # what the DB now contains (including fields untouched by this update).
    agent_settings = AgentSettings.from_session(updated_session or session)
    effective = helpers.resolve_agent_settings(agent_settings)
    effective = helpers.enforce_agent_settings_consistency(effective)

    try:
        await manager.send_to_session(
            session_id, session.project_id, project.directory, "",
            settings=effective, images=None, documents=None,
        )
    except RuntimeError as e:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session", "manager_busy", str(e)),
        ])

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )
