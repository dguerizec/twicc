"""
CLI entry point for TwiCC.

Lightweight dispatcher — subcommand modules must be imported lazily inside each
command function so that they never pay for Django startup.
"""

import os

import typer

from twicc.version import get_version

# Ensure Django settings are discoverable for all subcommands that call django.setup().
# Force to twicc.settings unless already set to a twicc-specific variant (e.g. for tests).
# This prevents a stray DJANGO_SETTINGS_MODULE from another project from breaking twicc.
if not os.environ.get("DJANGO_SETTINGS_MODULE", "").startswith("twicc.settings"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "twicc.settings"


def _normalize_project_id(project_id: str) -> str:
    """Ensure the project ID starts with a dash (auto-prepend if missing)."""
    if not project_id.startswith("-"):
        project_id = f"-{project_id}"
    return project_id


app = typer.Typer(
    name="twicc",
    help="TwiCC — The Web Interface for Claude and Codex.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"twicc {get_version()}")
        raise typer.Exit()


@app.callback()
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    """Launch the TwiCC server (default when no subcommand is given)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.run import main as run_main

    run_main()


@app.command()
def run() -> None:
    """Start the TwiCC server (you can commit thr `run` command)."""
    from twicc.cli.run import main as run_main

    run_main()


@app.command()
def projects(
    limit: int = typer.Option(20, help="Max number of projects to return."),
    offset: int = typer.Option(0, help="Skip first N projects."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects."),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only projects belonging to that workspace)."),
) -> None:
    """List all projects as JSON (ordered by most recently active)."""
    from twicc.cli.projects import main as projects_main

    projects_main(limit=limit, offset=offset, archived=include_archived, workspace=workspace)


@app.command()
def project(
    project_id: str = typer.Argument(help="The project ID (leading dash is optional)."),
) -> None:
    """Show a single project as JSON."""
    from twicc.cli.project import main as project_main

    project_main(_normalize_project_id(project_id))


@app.command()
def workspaces(
    limit: int = typer.Option(20, help="Max number of workspaces to return."),
    offset: int = typer.Option(0, help="Skip first N workspaces."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived workspaces."),
) -> None:
    """List all workspaces as JSON (in their stored order)."""
    from twicc.cli.workspaces import main as workspaces_main

    workspaces_main(limit=limit, offset=offset, archived=include_archived)


@app.command()
def workspace(
    workspace_id: str = typer.Argument(help="The workspace ID."),
) -> None:
    """Show a single workspace as JSON."""
    from twicc.cli.workspace import main as workspace_main

    workspace_main(workspace_id)


@app.command()
def sessions(
    project: str = typer.Option(None, help="Filter by project ID (leading dash is optional)."),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only sessions of projects in that workspace). Can be combined with --project."),
    limit: int = typer.Option(20, help="Max number of sessions to return."),
    offset: int = typer.Option(0, help="Skip first N sessions."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived sessions."),
) -> None:
    """List sessions as JSON (ordered by most recently active)."""
    from twicc.cli.sessions import main as sessions_main

    sessions_main(
        project=_normalize_project_id(project) if project is not None else None,
        workspace=workspace,
        limit=limit,
        offset=offset,
        archived=include_archived,
    )


session_app = typer.Typer(
    name="session",
    help="Inspect a session.",
    invoke_without_command=True,
)
app.add_typer(session_app)


@session_app.callback(invoke_without_command=True)
def _session_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="The session ID (for normal sessions or agents) to look up."),
) -> None:
    """Show a single session as JSON."""
    ctx.obj = session_id
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.session import main as session_main

    session_main(session_id)


@session_app.command()
def content(
    ctx: typer.Context,
    range: str = typer.Argument(help="Line number or range (e.g. '5' or '10-20')."),
) -> None:
    """Show session item(s) content as JSON."""
    from twicc.cli.session import content as session_content

    session_content(ctx.obj, range_str=range)


@session_app.command()
def messages(
    ctx: typer.Context,
    range: str = typer.Option(None, "--range", help="Restrict to a line number or range (e.g. '5' or '10-20')."),
    role: str = typer.Option(None, "--role", help="Filter by author: 'user' or 'assistant'."),
    limit: int = typer.Option(None, "--limit", help="Max number of messages to return (default: no limit)."),
    offset: int = typer.Option(0, "--offset", help="Skip first N messages."),
    tail: int = typer.Option(None, "--tail", help="Return the last N messages (mutually exclusive with --limit/--offset)."),
) -> None:
    """Show all user/assistant messages of a session as JSON (cross-provider)."""
    from twicc.cli.session import messages as session_messages

    session_messages(ctx.obj, range_str=range, role=role, limit=limit, offset=offset, tail=tail)


@session_app.command()
def agents(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of subagents to return."),
    offset: int = typer.Option(0, help="Skip first N subagents."),
) -> None:
    """List subagents of a session as JSON."""
    from twicc.cli.session import agents as session_agents

    session_agents(ctx.obj, limit=limit, offset=offset)


@app.command()
def usage() -> None:
    """Show the latest usage quota snapshot as JSON."""
    from twicc.cli.usage import main as usage_main

    usage_main()


@app.command()
def processes(
    provider: str = typer.Option(None, "--provider", help="Filter by backend provider (e.g. 'claude_code', 'codex')."),
    state: str = typer.Option(
        None,
        "--state",
        help=(
            "Filter by state: 'starting', 'assistant_turn' (actively generating), "
            "'awaiting_user_input' (blocked on a user click), or 'user_turn' "
            "(turn finished, awaiting next user message). 'dead' is never returned."
        ),
    ),
    limit: int = typer.Option(20, help="Max number of processes to return."),
    offset: int = typer.Option(0, help="Skip first N processes."),
) -> None:
    """List currently running processes of the live TwiCC instance as JSON."""
    from twicc.cli.processes import main as processes_main

    processes_main(
        provider=provider,
        state=state,
        limit=limit,
        offset=offset,
    )


process_app = typer.Typer(
    name="process",
    help="Inspect or control a session's live process.",
    invoke_without_command=True,
)
app.add_typer(process_app)


@process_app.callback(invoke_without_command=True)
def _process_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="The session ID of the running process."),
) -> None:
    """Show the currently running process for a session as JSON (default action)."""
    ctx.obj = session_id
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.process import main as process_main

    process_main(session_id)


@process_app.command(name="stop")
def process_stop(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the kill may still apply on the "
            "server side."
        ),
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable ANSI colors in human-readable output.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit a single JSON object on stdout instead of pretty text. "
            "Implies --no-color."
        ),
    ),
) -> None:
    """Stop the live agent process attached to the session.

    Equivalent to clicking the UI's *Stop process* button: asks the agent
    manager to kill the agent with ``reason="manual"``. Idempotent — if no
    live agent is currently attached, the command still exits 0.
    """
    from twicc.cli.process_stop import stop_cmd

    stop_cmd(
        ctx.obj,
        timeout=timeout,
        no_color=no_color,
        json_output=json_output,
    )


@app.command()
def status() -> None:
    """Report the live TwiCC backend's status as JSON (exit 0 only when running)."""
    from twicc.cli.status import main as status_main

    status_main()


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True, "help_option_names": []},
)
def claude(ctx: typer.Context) -> None:
    """Run the Claude Code CLI bundled with claude-agent-sdk."""
    from twicc.cli.claude import main as claude_main

    claude_main(ctx.args)


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False, "ignore_unknown_options": True, "help_option_names": []},
)
def codex(ctx: typer.Context) -> None:
    """Run the Codex CLI bundled with codex-app-server."""
    from twicc.cli.codex import main as codex_main

    codex_main(ctx.args)


@app.command()
def search(
    query: str = typer.Argument(help="Tantivy query string (e.g. 'websocket', 'body:websocket AND from_role:user')"),
    limit: int = typer.Option(20, help="Max number of hits."),
    offset: int = typer.Option(0, help="Skip first N hits."),
) -> None:
    """Query the TwiCC search index using raw Tantivy query syntax."""
    from twicc.cli.search import main as search_main

    search_main(query, limit=limit, offset=offset)


# ``create-session`` is registered directly from its module: the function
# already declares all its Typer options and performs lazy Django setup
# inside its body, so importing it here is cheap (no Django bootstrap).
from twicc.cli.create_session.command import create_session_cmd  # noqa: E402
app.command(name="create-session")(create_session_cmd)


# ``send-message`` follows the same pattern: lazy Django setup inside the
# function body keeps ``--help`` fast.
from twicc.cli.send_message.command import send_message_cmd  # noqa: E402
app.command(name="send-message")(send_message_cmd)


# ``update-session`` is a Typer sub-app: ``twicc update-session <ID>
# <subcommand>`` (only ``settings`` for now; ``title``, ``archive``,
# ``pin``, ``stop`` will plug into the same sub-app later).
from twicc.cli.update_session.command import update_session_app  # noqa: E402
app.add_typer(update_session_app)


# ``password`` is a Typer sub-app (set/clear/status). The module is lightweight
# (no Django setup) so importing it at module load is cheap.
from twicc.cli.password import app as password_app  # noqa: E402
app.add_typer(password_app)


# ``whoami`` resolves the TwiCC session owning the calling process via PID
# ancestry. The function performs lazy Django setup inside its body.
from twicc.cli.whoami import whoami_cmd  # noqa: E402
app.command("whoami")(whoami_cmd)


def main() -> None:
    """Entry point for ``pyproject.toml`` scripts and ``__main__.py``."""
    app()
