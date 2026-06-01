"""Shared projection + serialization helpers for the ``process`` CLI family.

Centralizes the virtual-state vocabulary (``ProcessRun.state`` +
``ProcessRun.awaiting_user_input`` → one of 5 values) and the row serialization
shared by:

- ``processes.py`` (``twicc processes``)
- ``process.py`` (``twicc process <ID>``)
- ``process_wait.py`` (``twicc process <ID> wait <STATUS>...``)
- ``processes_get.py`` (``twicc processes get <SESSION_ID>...``)

The first two never observe a DEAD row (their queryset filters it out at the
SQL level). ``wait`` and ``get`` are the only callers that can: they have to
detect the "no live process" condition as a matchable / reportable state, so
they pull the most-recent row regardless of state and let the projection
collapse DEAD (and absence of a row) to the virtual value ``"dead"``.
"""

from __future__ import annotations

from twicc.agent.states import AgentState

DEAD_VIRTUAL_STATE = "dead"
AWAITING_VIRTUAL_STATE = "awaiting_user_input"

VALID_VIRTUAL_STATES = frozenset({
    AgentState.STARTING.value,
    AgentState.ASSISTANT_TURN.value,
    AgentState.USER_TURN.value,
    AWAITING_VIRTUAL_STATE,
    DEAD_VIRTUAL_STATE,
})

# Subset accepted by ``processes`` and ``process`` (their --state filter must
# reject 'dead' to keep the "live processes only" guarantee).
LIVE_VIRTUAL_STATES = VALID_VIRTUAL_STATES - {DEAD_VIRTUAL_STATE}


def project_virtual_state(row) -> str:
    """Project a ``ProcessRun`` row onto the 5-value virtual vocabulary.

    Rules (in order):

    - ``row is None`` or ``row.state == DEAD`` → ``"dead"``
    - ``row.awaiting_user_input`` → ``"awaiting_user_input"``
      (always implies ``state == ASSISTANT_TURN`` by construction)
    - otherwise → ``row.state`` (``"starting"`` / ``"assistant_turn"`` /
      ``"user_turn"``)
    """
    if row is None or row.state == AgentState.DEAD.value:
        return DEAD_VIRTUAL_STATE
    if row.awaiting_user_input:
        return AWAITING_VIRTUAL_STATE
    return row.state


def serialize_process_row(row, session) -> dict:
    """Serialize a (ProcessRun, Session) pair into the common output schema.

    ``row`` MUST be a real ``ProcessRun`` (use :func:`serialize_dead_process_row`
    for the "no row, project to dead" case). ``session`` may be ``None``
    when the watcher has not created the ``Session`` row yet — title and
    ``project_id`` fall back to ``None`` then.
    """
    return {
        "id": row.pk,
        "provider": row.provider,
        "session_id": row.session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": project_virtual_state(row),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_state_change_at": (
            row.last_state_change_at.isoformat()
            if row.last_state_change_at
            else None
        ),
        "pid": row.agent_pid,
    }


def serialize_dead_process_row(session, *, session_id: str) -> dict:
    """Serialize the "no live ProcessRun row" case into the common output schema.

    Returns the same shape as :func:`serialize_process_row` with ``state="dead"``
    and every row-bound field (``id``, ``started_at``, ``last_state_change_at``,
    ``pid``) set to ``None``. ``provider``, ``session_title`` and ``project_id``
    fall back to the ``Session`` row when one exists, else ``None``.
    """
    return {
        "id": None,
        "provider": session.provider if session is not None else None,
        "session_id": session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": DEAD_VIRTUAL_STATE,
        "started_at": None,
        "last_state_change_at": None,
        "pid": None,
    }


def serialize_wait_result(
    row,
    session,
    *,
    session_id: str,
    matched_state: str,
) -> dict:
    """Serialize the result of a ``wait`` match.

    Adds ``matched_state`` to the standard schema. Accepts ``row = None``
    for the "wait dead matched, no row" case — in that branch the row-bound
    fields (``id``, ``provider``, ``started_at``, ``last_state_change_at``,
    ``pid``) are ``null`` and ``state`` is forced to ``"dead"``. The
    ``session_id`` argument is the one the CLI received, used as fallback
    when no row exists to carry it.
    """
    if row is None:
        return {
            "id": None,
            "provider": session.provider if session is not None else None,
            "session_id": session_id,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "state": DEAD_VIRTUAL_STATE,
            "matched_state": matched_state,
            "started_at": None,
            "last_state_change_at": None,
            "pid": None,
        }
    data = serialize_process_row(row, session)
    data["matched_state"] = matched_state
    return data


def serialize_get_result(
    row,
    session,
    *,
    session_id: str,
    session_known: bool,
) -> dict:
    """Serialize one entry of a ``processes get`` lookup.

    Adds ``session_known`` to the standard schema. Accepts ``row = None``
    for session_ids whose live ``ProcessRun`` row is missing — in that
    branch the row-bound fields (``id``, ``started_at``,
    ``last_state_change_at``, ``pid``) are ``null``; ``provider``,
    ``session_title`` and ``project_id`` fall back to the ``Session`` row
    when one exists, else are also ``null``.

    ``session_known`` is the caller's claim that TwiCC has at least one
    trace of the session (a ``Session`` row OR any ``ProcessRun`` row,
    even DEAD). ``False`` flags typos, periodic cleanup, or a foreign
    session_id from a stale script — distinguishing them from a session
    that genuinely existed but whose live process is gone.
    """
    if row is None:
        return {
            "id": None,
            "provider": session.provider if session is not None else None,
            "session_id": session_id,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "state": DEAD_VIRTUAL_STATE,
            "session_known": session_known,
            "started_at": None,
            "last_state_change_at": None,
            "pid": None,
        }
    data = serialize_process_row(row, session)
    data["session_known"] = session_known
    return data
