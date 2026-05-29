"""CLI implementation for the ``twicc processes`` subcommand."""

import sys

import orjson


def main(*, provider: str | None = None, limit: int = 20, offset: int = 0) -> None:
    """List currently running processes (live ProcessRuns) of the running TwiCC.

    Scopes to ``twicc_pid`` equal to the PID recorded in ``twicc.info.json``
    and excludes ``state=DEAD`` rows — a DEAD row never represents a
    running process (Claude Code keeps DEAD rows around solely so the boot
    cron restart can reclaim them, never to advertise a live worker).
    """
    import django

    django.setup()

    from twicc.agent.states import AgentState
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Session

    info = resolve_live_twicc_or_exit()

    qs = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
    )

    if provider is not None:
        qs = qs.filter(provider=provider)

    rows = list(qs[offset : offset + limit])

    # Enrich with the matching Session's title and project_id when the
    # session row has already been created by the watcher. Brand-new
    # sessions that haven't reached their first JSONL line yet have no
    # Session row, so those fields fall back to ``None``.
    session_ids = [r.session_id for r in rows]
    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=session_ids).only("id", "title", "project_id")
    }

    data = [_serialize(row, sessions_by_id.get(row.session_id)) for row in rows]

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def _serialize(row, session) -> dict:
    return {
        "id": row.pk,
        "provider": row.provider,
        "session_id": row.session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": row.state,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_state_change_at": (
            row.last_state_change_at.isoformat() if row.last_state_change_at else None
        ),
        "pid": row.agent_pid,
    }
