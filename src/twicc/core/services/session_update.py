"""Apply updates to an existing agent session.

Called by :class:`PendingSessionsWatcher` when the CLI drops a request file
whose ``kind`` matches one of the supported update actions:

- ``kind="update_settings"`` → :func:`update_session_settings_from_payload`.
  Mirrors the settings-only branch of
  :meth:`twicc.asgi.WSConsumer._handle_send_message`: write the new values
  to the ``Session`` row under the DB write lock, broadcast
  ``session_updated`` out of the lock, and propagate to the live agent via
  ``manager.send_to_session(text="")`` when a process is attached.
- ``kind="update_title"`` → :func:`update_session_title_from_payload`.
  Mirrors the title branch of ``PATCH /api/projects/.../sessions/<id>/`` in
  :mod:`twicc.views`: validate via the provider's ``validate_title``, write
  under the DB write lock, re-index the full-text search document, ask the
  provider to persist into its backing store (JSONL custom-title entry for
  Claude Code, ``thread/name/set`` for Codex), then broadcast
  ``session_updated``.

The functions do NOT raise for business-rule errors (missing session,
provider disabled, etc.); they return an :class:`UpdateSessionResult` with
``success=False`` and a list of structured error tuples. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

import asyncio
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


async def _lookup_session_for_update(
    session_id: str | None,
):
    """Resolve a session for any update operation.

    Returns ``(session, project, provider, None)`` on success, or
    ``(None, None, None, error_result)`` where ``error_result`` is the
    :class:`UpdateSessionResult` the caller must return as-is. Centralises
    the same business-rule guards used by every ``update_*`` service so a
    new sub-command (title, archive, pin, stop, ...) only writes its
    action-specific logic on top.
    """
    from twicc.core.models import Session, SessionType

    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_not_found",
                                f"Session {session_id!r} not found"),
        ])
    if session.type == SessionType.SUBAGENT:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "is_subagent",
                                f"Session {session_id!r} is a subagent; "
                                "subagents cannot be updated directly. "
                                "Target the parent session instead."),
        ])
    if session.stale:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_stale",
                                f"Session {session_id!r} is stale "
                                "(its JSONL file no longer exists on disk)"),
        ])

    project = session.project
    if project is None or not project.directory:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "project_no_directory",
                                f"Session {session_id!r} has no project directory"),
        ])

    try:
        provider = Provider(session.provider)
    except ValueError:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "unknown_provider",
                                f"Session {session_id!r} has unknown provider "
                                f"{session.provider!r}"),
        ])

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("provider", "provider_disabled", str(e)),
        ])

    return session, project, provider, None


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
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service (``session_not_found``, ``is_subagent``,
      ``session_stale``, ``project_no_directory``, ``unknown_provider``,
      ``provider_disabled``).
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

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

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


async def update_session_title_from_payload(payload: dict) -> UpdateSessionResult:
    """Set a new title on an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the target session (required).
    - ``title``: new title string (required). Validated via the provider's
      ``validate_title`` (trim, non-empty, ≤ ``MAX_TITLE_LENGTH``).

    Business-rule rejections (returned as ``success=False``):
    - ``invalid_title``: ``validate_title`` rejected the trimmed value.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service.
    """
    session_id = payload.get("session_id")
    raw_title = payload.get("title")

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if raw_title is None:
        errors.append(UpdateSessionError("title", "missing", "title is required"))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    helpers = get_provider_helpers(provider)
    validation = helpers.validate_title(raw_title)
    if validation.error:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("title", "invalid_title", validation.error),
        ])
    title = validation.title

    # --- DB write under the write lock ---------------------------------
    # Matches ``views.py`` PATCH session: ``Session.asave(update_fields=["title"])``
    # under the lock, then propagate to search + provider rename outside.
    session.title = title
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["title"])
    )

    # --- search reindex (non-critical) ---------------------------------
    # The title is part of the full-text search document; if reindex fails,
    # the next full-search rebuild catches up. Don't fail the whole update.
    from twicc import search
    if search.is_initialized():
        try:
            await asyncio.to_thread(search.reindex_session, session_id)
        except Exception:
            logger.warning(
                "[update_session_title] search reindex failed for %s",
                session_id, exc_info=True,
            )

    # --- provider rename (non-critical) --------------------------------
    # Claude Code appends a ``custom-title`` JSONL entry and protects it
    # against stale CLI re-appends; Codex calls ``thread/name/set``. The
    # DB is already updated, so a transient failure here is logged and
    # the watcher / next resume reconciles.
    try:
        await helpers.rename_session(session_id, title)
    except Exception:
        logger.warning(
            "[update_session_title] provider rename_session failed for %s",
            session_id, exc_info=True,
        )

    # --- broadcast ----------------------------------------------------
    # Always broadcast explicitly even though the provider rename usually
    # triggers a file-watcher broadcast too: the CLI needs a deterministic
    # outcome and the broadcast is idempotent for UI consumers.
    from twicc.core.serializers import serialize_session
    channel_layer = get_channel_layer()
    if channel_layer is not None:
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

    logger.info("[update_session_title] session=%s title=%r", session_id, title)

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )
