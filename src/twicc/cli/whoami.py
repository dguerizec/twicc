"""``twicc whoami`` — identify the session that owns the calling process."""

from __future__ import annotations

import sys

import orjson
import typer


def whoami_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit a single JSON object on stdout instead of pretty text. "
            "Exit code is still 0 on success, 1 when no session is found."
        ),
    ),
) -> None:
    """Print details of the session that owns the calling process.

    Walks the PID ancestry from the current process upward and matches
    against the live agents tracked by TwiCC. When a match is found,
    prints a JSON object with: ``session_id``, ``title``, ``project_id``,
    ``project_directory``, ``current_working_directory`` (resolved
    from tool_use paths, may differ from ``project_directory`` when
    the agent works in a worktree or other repo), the resolved
    ``agent_settings``, the full ``session``
    payload (same as ``twicc session <ID>``), and the matching
    ``process`` row.

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import django

    django.setup()

    from twicc.agent.states import AgentState
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._process_state import (
        serialize_dead_process_row,
        serialize_process_row,
    )
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Project
    from twicc.core.serializers import serialize_session
    from twicc.pending_titles import get_pending_title
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    session = resolve_current_session()
    if session is None:
        msg = (
            "No TwiCC session found in PID ancestry. whoami is only "
            "meaningful from inside an active agent session."
        )
        if json_output:
            sys.stdout.buffer.write(orjson.dumps({"error": msg}))
            sys.stdout.buffer.write(b"\n")
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(1)

    helpers = get_provider_helpers(session.provider)
    resolved_settings = helpers.resolve_agent_settings(AgentSettings.from_session(session))

    project_directory = None
    if session.project_id:
        project = Project.objects.filter(id=session.project_id).only("directory").first()
        if project is not None:
            project_directory = project.directory

    info = resolve_live_twicc_or_exit()
    row = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id=session.id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )
    if row is not None:
        process = serialize_process_row(row, session)
    else:
        process = serialize_dead_process_row(session, session_id=session.id)

    data = {
        "session_id": session.id,
        "title": get_pending_title(session.id) or session.title,
        "project_id": session.project_id,
        "project_directory": project_directory,
        "current_working_directory": session.git_directory,
        "agent_settings": resolved_settings._asdict(),
        "session": serialize_session(session),
        "process": process,
    }
    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
