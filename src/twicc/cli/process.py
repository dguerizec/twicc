"""CLI implementation for the ``twicc process`` subcommand."""

from twicc.cli._output import emit_error, emit_json


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
    from twicc.cli._process_state import serialize_process_row
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
        emit_error(
            f"Error: no running process for session '{session_id}'.",
            code=1,
        )

    session = (
        Session.objects.filter(id=session_id).only("id", "title", "project_id").first()
    )

    data = serialize_process_row(row, session)

    emit_json(data)
