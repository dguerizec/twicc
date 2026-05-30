"""hide_session / unhide_session — orchestrate the hidden-flag flip.

Both entry points are async (they touch DB + broadcast + FTS), receive
the already-fetched ``Session`` row, and return a structured result the
caller turns into a status file or WS payload. They share private
helpers for the recompute side-effects.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


class SessionVisibilityError(NamedTuple):
    field: str
    code: str
    message: str


class SessionVisibilityResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionVisibilityError] | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def hide_session(session) -> SessionVisibilityResult:
    """Flip hidden False -> True.

    Pre-conditions: session is type=SESSION, permission_mode is in the
    hidden whitelist for its provider, question_widget is not True
    (Claude Code).
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if session.hidden:
        return _ok(session)  # no-op: already hidden
    errors = _check_hidden_invariants(session)
    if errors:
        return _fail(session, errors)

    await _apply_flip(session, new_hidden=True)
    await _broadcast_session_removed(session.id)
    await _broadcast_project_updated(session.project_id)
    return _ok(session)


async def unhide_session(session) -> SessionVisibilityResult:
    """Flip hidden True -> False.

    No invariant checks beyond type=SESSION: the session re-enters the
    user surface, the user can reconfigure permission_mode / question_widget
    freely afterwards.
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if not session.hidden:
        return _ok(session)  # no-op: already visible

    await _apply_flip(session, new_hidden=False)
    await _broadcast_session_updated(session)
    await _broadcast_project_updated(session.project_id)
    return _ok(session)


# ---------------------------------------------------------------------------
# Pre-conditions
# ---------------------------------------------------------------------------


def _check_type_session(session) -> list[SessionVisibilityError]:
    from twicc.core.models import SessionType
    if session.type != SessionType.SESSION:
        return [SessionVisibilityError(
            "type", "not_top_level",
            "Only top-level sessions (type=SESSION) can be hidden; "
            f"got type={session.type!r}.",
        )]
    return []


def _check_hidden_invariants(session) -> list[SessionVisibilityError]:
    """Run validate_hidden_constraints against the current Session row.

    We read the columns directly (the session is already saved, no preset to merge).
    """
    from twicc.cli._session_request.validation import validate_hidden_constraints
    from twicc.providers.helpers import AgentSettings

    settings = AgentSettings.from_session(session)
    vlist = validate_hidden_constraints(
        session.provider, settings, hidden=True,
    )
    return [SessionVisibilityError(v.field, v.code, v.message) for v in vlist]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def _apply_flip(session, *, new_hidden: bool) -> None:
    """Save the flag, recompute counters, reindex FTS — all in async hops."""
    from twicc.search import reindex_session

    @sync_to_async
    def _save_and_recompute():
        # Step 1: Toggle the flag and persist. Must succeed; if it raises we abort.
        session.hidden = new_hidden
        session.save(update_fields=["hidden"])

        # Steps 2-4: best-effort. A failure here leaves the flag set but counters/FTS
        # slightly stale; the next refresh / restart will heal.
        try:
            # 2. Recompute sessions_count on the Project.
            #    update_project_metadata takes project_id (str), not a Project instance.
            from twicc.projects import update_project_metadata
            update_project_metadata(session.project_id)

            # 3. Collect dates impacted by the session's items, then recompute
            #    PeriodicActivity for each (DailyActivity + WeeklyActivity,
            #    per-project + global).
            from twicc.core.models import SessionItem, PeriodicActivity
            from twicc.core.enums import Provider

            days = {
                d for d, in SessionItem.objects
                .filter(session=session, timestamp__isnull=False)
                .values_list("timestamp__date")
                .distinct()
            }
            if days:
                provider_enum = Provider(session.provider)
                PeriodicActivity.recalculate_for_days(
                    session.project_id, days, provider_enum,
                )

            # 4. Reindex the session document — the `hidden` Tantivy field
            #    is now stale.
            reindex_session(session.id)
        except Exception:
            logger.exception(
                "session_visibility flip side-effects failed for session %s "
                "(hidden=%s). The DB flag is set; counters/FTS may be stale "
                "until next refresh.",
                session.id, new_hidden,
            )

    await _save_and_recompute()


# ---------------------------------------------------------------------------
# Broadcasts
# ---------------------------------------------------------------------------


async def _broadcast_session_removed(session_id: str) -> None:
    """Emit a session_removed WS event so connected clients drop the row."""
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "session_removed", "session_id": session_id},
    })


async def _broadcast_session_updated(session) -> None:
    """Emit a session_updated WS event with the freshly visible session."""
    from twicc.core.serializers import serialize_session
    layer = get_channel_layer()
    if layer is None:
        return
    payload = await sync_to_async(serialize_session)(session)
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "session_updated", "session": payload},
    })


async def _broadcast_project_updated(project_id: str) -> None:
    """Emit a project_updated WS event (sessions_count + cost may have changed)."""
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    layer = get_channel_layer()
    if layer is None:
        return
    try:
        if project := await sync_to_async(Project.objects.filter(id=project_id).first)():
            await layer.group_send("updates", {
                "type": "broadcast",
                "data": {"type": "project_updated", "project": serialize_project(project)},
            })
    except Exception:
        logger.exception("Failed to broadcast project_updated for %s", project_id)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _ok(session) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=True,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=None,
    )


def _fail(session, errors) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=False,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=errors,
    )
