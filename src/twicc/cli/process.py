"""CLI implementation for the ``twicc process`` subcommand."""

import sys

import orjson


def main(session_id: str) -> None:
    """Show the currently running process (live ProcessRun) for a session as JSON.

    Scopes to ``twicc_pid`` equal to the PID recorded in ``twicc.info.json``
    and excludes ``state=DEAD`` rows. Exits with status 1 when no live
    ProcessRun matches — including the case where the session is alive in
    the DB but its process died and its row was either deleted or kept as
    DEAD for the boot cron restart. Use ``twicc processes`` to see which
    sessions are currently running.

    If multiple non-DEAD rows match the same session_id (a narrow window
    around cron-driven restarts, before the first USER_TURN purges the
    older row), the most recent one by ``started_at`` is returned.
    """
    import django

    django.setup()

    from twicc.agent.states import AgentState
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Session

    info = resolve_live_twicc_or_exit()

    row = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id=session_id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )
    if row is None:
        print(
            f"Error: no running process for session '{session_id}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    session = (
        Session.objects.filter(id=session_id).only("id", "title", "project_id").first()
    )

    # Project the persisted ``state`` + ``awaiting_user_input`` columns onto
    # the same 4-value vocabulary as ``twicc processes`` (see that module's
    # ``main`` docstring for the rule). ``awaiting_user_input`` always implies
    # ``state=ASSISTANT_TURN`` in DB, so the projection is lossless.
    from twicc.cli.processes import VIRTUAL_AWAITING_STATE
    virtual_state = VIRTUAL_AWAITING_STATE if row.awaiting_user_input else row.state

    data = {
        "id": row.pk,
        "provider": row.provider,
        "session_id": row.session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": virtual_state,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_state_change_at": (
            row.last_state_change_at.isoformat() if row.last_state_change_at else None
        ),
        "pid": row.agent_pid,
    }

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
