"""
CLI entry point for TwiCC.

Lightweight dispatcher — subcommand modules must be imported lazily inside each
command function so that they never pay for Django startup.
"""

import os

import typer

from twicc.cli._drop_request.project import derive_project_id
from twicc.version import get_version

# Ensure Django settings are discoverable for all subcommands that call django.setup().
# Force to twicc.settings unless already set to a twicc-specific variant (e.g. for tests).
# This prevents a stray DJANGO_SETTINGS_MODULE from another project from breaking twicc.
if not os.environ.get("DJANGO_SETTINGS_MODULE", "").startswith("twicc.settings"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "twicc.settings"


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


projects_app = typer.Typer(
    name="projects",
    help="List projects, or look up specific project_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(projects_app)


@projects_app.callback(invoke_without_command=True)
def _projects_default(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of projects to return."),
    offset: int = typer.Option(0, help="Skip first N projects."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects."),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only projects belonging to that workspace)."),
) -> None:
    """List all projects as JSON (ordered by most recently active, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.projects import main as projects_main

    projects_main(limit=limit, offset=offset, archived=include_archived, workspace=workspace)


@projects_app.command(name="get")
def _projects_get(
    project_ids: list[str] = typer.Argument(
        ...,
        metavar="PROJECT...",
        help=(
            "One or more projects to look up. Each value is a project ID "
            "(with or without leading dash) or a directory path (absolute or "
            "relative); paths are resolved via realpath and converted to "
            "their canonical id. The output mirrors the input order "
            "(duplicates collapsed by canonical id, first occurrence wins). "
            "Each entry is either the full project metadata or a placeholder "
            "with `known: false` when no Project row exists for that id "
            "(applies the same way to a path that doesn't match any known "
            "project). Archived projects are returned just like active "
            "ones — the listing filter doesn't apply when you name explicit "
            "projects."
        ),
    ),
) -> None:
    """Look up projects by id or path (placeholder for missing, includes archived).

    Unlike ``twicc projects``, ``get`` takes no filter flags: when the
    caller names the projects it cares about, the archived-by-default
    filter would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.projects_get import main as projects_get_main

    projects_get_main([derive_project_id(pid)[0] for pid in project_ids])


@app.command()
def project(
    project_id: str = typer.Argument(
        help=(
            "Project ID (with or without leading dash) or directory path "
            "(absolute or relative)."
        ),
    ),
) -> None:
    """Show a single project as JSON."""
    from twicc.cli.project import main as project_main

    project_main(derive_project_id(project_id)[0])


workspaces_app = typer.Typer(
    name="workspaces",
    help="List workspaces, or look up specific workspace_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(workspaces_app)


@workspaces_app.callback(invoke_without_command=True)
def _workspaces_default(
    ctx: typer.Context,
    limit: int = typer.Option(20, help="Max number of workspaces to return."),
    offset: int = typer.Option(0, help="Skip first N workspaces."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived workspaces."),
) -> None:
    """List all workspaces as JSON (in their stored order, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.workspaces import main as workspaces_main

    workspaces_main(limit=limit, offset=offset, archived=include_archived)


@workspaces_app.command(name="get")
def _workspaces_get(
    workspace_ids: list[str] = typer.Argument(
        ...,
        metavar="WORKSPACE_ID...",
        help=(
            "One or more workspace IDs to look up. The output mirrors the "
            "input order (duplicates collapsed, first occurrence wins). "
            "Each entry is either the full workspace definition or a "
            "placeholder with `known: false` when no workspace exists for "
            "that id. Archived workspaces are returned just like active "
            "ones — the listing filter doesn't apply when you name "
            "explicit ids."
        ),
    ),
) -> None:
    """Look up workspaces by id (placeholder for missing, includes archived).

    Unlike ``twicc workspaces``, ``get`` takes no filter flags: when the
    caller names the workspaces it cares about, the archived-by-default
    filter would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.workspaces_get import main as workspaces_get_main

    workspaces_get_main(workspace_ids)


@app.command()
def workspace(
    workspace_id: str = typer.Argument(help="The workspace ID."),
) -> None:
    """Show a single workspace as JSON."""
    from twicc.cli.workspace import main as workspace_main

    workspace_main(workspace_id)


sessions_app = typer.Typer(
    name="sessions",
    help="List sessions, or look up specific session_ids in batch.",
    invoke_without_command=True,
)
app.add_typer(sessions_app)


@sessions_app.callback(invoke_without_command=True)
def _sessions_default(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        help=(
            "Filter by project: either a project ID (with or without "
            "leading dash) or a directory path (absolute or relative)."
        ),
    ),
    workspace: str = typer.Option(None, "--workspace", help="Filter by workspace ID (only sessions of projects in that workspace). Can be combined with --project."),
    limit: int = typer.Option(20, help="Max number of sessions to return."),
    offset: int = typer.Option(0, help="Skip first N sessions."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived sessions."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include hidden sessions in the listing."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Show ONLY hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to sessions spawned by the given session_id, or 'self' for the "
            "current session. Implies --include-hidden by default: a filiation query "
            "shows every matching child whatever its visibility. Add --only-hidden to "
            "narrow to hidden children, or pass an explicit ID and rely on the JSON "
            "output's `hidden` field to filter further."
        ),
    ),
) -> None:
    """List sessions as JSON (ordered by most recently active, default action)."""
    if ctx.invoked_subcommand is not None:
        return

    if include_hidden and only_hidden:
        typer.echo("Error: --include-hidden and --only-hidden are mutually exclusive.", err=True)
        raise typer.Exit(2)

    from twicc.cli.sessions import main as sessions_main

    sessions_main(
        project=derive_project_id(project)[0] if project is not None else None,
        workspace=workspace,
        limit=limit,
        offset=offset,
        archived=include_archived,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
    )


@sessions_app.command(name="get")
def _sessions_get(
    session_ids: list[str] = typer.Argument(
        ...,
        metavar="SESSION_ID...",
        help=(
            "One or more session IDs to look up. The output mirrors the input "
            "order (duplicates collapsed, first occurrence wins). Each entry "
            "is either the full session metadata or a placeholder with "
            "`known: false` when no Session row exists for that id. "
            "Subagents, archived and hidden sessions are returned just like "
            "regular ones — the listing filters don't apply when you name "
            "explicit ids."
        ),
    ),
) -> None:
    """Look up sessions by id (placeholder for missing, includes subagents).

    Unlike ``twicc sessions``, ``get`` takes no filter flags: when the
    caller names the sessions it cares about, layering archived /
    hidden / subagent filters on top would only blur the meaning of the
    placeholder rows.
    """
    from twicc.cli.sessions_get import main as sessions_get_main

    sessions_get_main(session_ids)


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
def topology(
    session_id: str = typer.Argument(
        help="Session ID to anchor the topology, or 'self' from inside an agent session.",
    ),
    processes: bool = typer.Option(
        True,
        "--processes/--no-processes",
        help=(
            "Include compact live process state when a TwiCC backend is running. "
            "If no backend is running, topology is still returned with process "
            "data marked unavailable."
        ),
    ),
) -> None:
    """Show the spawned-session tree containing a session as JSON."""
    from twicc.cli.topology import main as topology_main

    topology_main(session_id, include_processes=processes)


processes_app = typer.Typer(
    name="processes",
    help="List live TwiCC processes, or look up specific session_ids.",
    invoke_without_command=True,
)
app.add_typer(processes_app)


@processes_app.callback(invoke_without_command=True)
def _processes_default(
    ctx: typer.Context,
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
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include processes of hidden sessions."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Show ONLY processes of hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to processes of sessions spawned by the given session_id, or 'self' "
            "for the current session. Implies --include-hidden by default: a filiation "
            "query surfaces every matching child whatever its visibility."
        ),
    ),
) -> None:
    """List currently running processes of the live TwiCC instance as JSON (default action)."""
    if ctx.invoked_subcommand is not None:
        return

    if include_hidden and only_hidden:
        typer.echo("Error: --include-hidden and --only-hidden are mutually exclusive.", err=True)
        raise typer.Exit(2)

    from twicc.cli.processes import main as processes_main

    processes_main(
        provider=provider,
        state=state,
        limit=limit,
        offset=offset,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
    )


@processes_app.command(name="get")
def _processes_get(
    session_ids: list[str] = typer.Argument(
        ...,
        metavar="SESSION_ID...",
        help=(
            "One or more session IDs to look up. The output mirrors the input "
            "order (duplicates collapsed, first occurrence wins). Each entry "
            "is either the live process row or a placeholder with state=\"dead\" "
            "when no live process exists for that ID; a session_known flag "
            "distinguishes typos from genuinely-stopped sessions."
        ),
    ),
) -> None:
    """Look up live process state for one or more session_ids (placeholder for missing).

    Unlike ``twicc processes``, ``get`` takes no filter flags: when the
    caller names the sessions it cares about, layering ``--provider`` /
    ``--state`` would only blur the meaning of the placeholder rows.
    """
    from twicc.cli.processes_get import main as processes_get_main

    processes_get_main(session_ids)


@processes_app.command(name="stop")
def _processes_stop(
    session_ids: list[str] = typer.Argument(
        ...,
        metavar="SESSION_ID...",
        help=(
            "One or more session IDs whose live agent process should be "
            "stopped. The output mirrors the input order (duplicates "
            "collapsed, first occurrence wins). Each entry carries a "
            "`status` (stopped/rejected/failed/timeout/skipped_*) plus the "
            "same metadata (provider, title, project_id) as `get`."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status across the whole "
            "batch (drops are processed in parallel server-side, so this is "
            "a wall-clock budget, not N×30). Entries with no final status "
            "by the deadline are reported with status=\"timeout\". Must be > 0."
        ),
    ),
) -> None:
    """Batch-stop live agent processes (idempotent, tolerant to skipped IDs).

    Pre-checks each ``session_id`` locally before dropping the kill request.
    Session_ids that fail the pre-check (unknown, subagent, stale, no
    directory, unknown provider) get a ``skipped_*`` status with no drop
    emitted; valid ones get a ``process:stop`` drop file and a poll for
    the server's final answer. Exit 0 always when the command completes —
    callers inspect each entry's ``status`` for the per-id outcome.
    """
    from twicc.cli.processes_stop import stop_cmd

    stop_cmd(session_ids, timeout=timeout)


@processes_app.command(name="wait")
def _processes_wait(
    items: list[str] = typer.Argument(
        ...,
        metavar="ITEM...",
        help=(
            "A single list mixing session_ids and statuses, auto-discriminated "
            "by value. Any item whose value is in (starting, assistant_turn, "
            "awaiting_user_input, user_turn, dead) is a status; everything "
            "else is a session_id. By convention pass session_ids first and "
            "statuses last (so a typo doesn't accidentally classify as a "
            "session_id); internally the order is irrelevant."
        ),
    ),
    timeout: float = typer.Option(
        ...,
        "--timeout",
        help=(
            "Required. Seconds to wait before giving up (exit 5). Must be > 0. "
            "Wall-clock budget for the entire batch (all session_ids are "
            "polled in parallel)."
        ),
    ),
    wait_all: bool = typer.Option(
        True,
        "--all/--first",
        help=(
            "--all (default): wait until EVERY active session_id has matched "
            "at least one status. --first: stop as soon as one has matched. "
            "Inactive (skipped_unknown) session_ids participate in neither."
        ),
    ),
    transition: bool = typer.Option(
        False,
        "--transition",
        help=(
            "Only evaluate a match for a session_id after observing at least "
            "one state transition since the initial snapshot. Applied "
            "per-session — each id must transition before it can match."
        ),
    ),
) -> None:
    """Block until multiple session_ids reach matching virtual states.

    Unknown session_ids (no Session row AND no ProcessRun for this TwiCC)
    are skipped silently and do NOT participate in --all / --first.
    If every session_id is skipped, exits 0 (vacuous truth — nothing to
    wait for).
    """
    from twicc.cli.processes_wait import wait_cmd

    wait_cmd(
        items,
        timeout=timeout,
        wait_all=wait_all,
        transition=transition,
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


@process_app.command(name="wait")
def process_wait(
    ctx: typer.Context,
    statuses: list[str] = typer.Argument(
        ...,
        metavar="STATUS...",
        help=(
            "One or more virtual states to wait for (any-of match). "
            "Valid values: starting, assistant_turn, awaiting_user_input, "
            "user_turn, dead. 'dead' matches when no live ProcessRun "
            "exists for the session."
        ),
    ),
    timeout: float = typer.Option(
        ...,
        "--timeout",
        help=(
            "Required. Seconds to wait for a matching state before giving "
            "up (exit 5). Must be > 0. No default — pass --timeout=N "
            "explicitly to bound the wait."
        ),
    ),
    transition: bool = typer.Option(
        False,
        "--transition",
        help=(
            "Only evaluate the match after observing at least one state "
            "transition since the initial snapshot. Useful to wait for the "
            "next change rather than the current value. Note: 'wait dead "
            "--transition' on an already-dead session can never match (the "
            "row is frozen) and will always timeout."
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
            "Emit only the final JSON object on stdout (no progress lines). "
            "Implies --no-color."
        ),
    ),
) -> None:
    """Block until the live process reaches any of the listed states.

    Polls the DB locally every 250 ms; the live TwiCC writes process
    transitions to the same row this command reads. Exits 0 on match,
    5 on timeout, 2 if TwiCC is not running, 1 on validation errors.
    """
    from twicc.cli.process_wait import wait_cmd

    wait_cmd(
        ctx.obj,
        statuses,
        timeout=timeout,
        transition=transition,
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
    limit: int = typer.Option(20, help="Max number of session groups to return."),
    offset: int = typer.Option(0, help="Skip first N session groups."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include hidden sessions in search results."),
    only_hidden: bool = typer.Option(False, "--only-hidden", help="Search ONLY hidden sessions (mutually exclusive with --include-hidden)."),
    spawned_by: str = typer.Option(
        None,
        "--spawned-by",
        help=(
            "Filter to sessions spawned by the given session_id, or 'self' for the "
            "current session. Implies --include-hidden by default: a filiation query "
            "matches every spawned child whatever its visibility."
        ),
    ),
) -> None:
    """Query the TwiCC search index using raw Tantivy query syntax."""
    if include_hidden and only_hidden:
        typer.echo("Error: --include-hidden and --only-hidden are mutually exclusive.", err=True)
        raise typer.Exit(2)

    from twicc.cli.search import main as search_main

    search_main(
        query,
        limit=limit,
        offset=offset,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
    )


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


# ``create-workspace`` / ``update-workspace`` / ``delete-workspace`` write
# to ``workspaces.json`` through the drop-request protocol. The functions
# declare all their Typer options inline and perform lazy Django setup
# inside their body, so importing them here stays cheap.
from twicc.cli.create_workspace import create_workspace_cmd  # noqa: E402
app.command(name="create-workspace")(create_workspace_cmd)

from twicc.cli.update_workspace import update_workspace_cmd  # noqa: E402
app.command(name="update-workspace")(update_workspace_cmd)

from twicc.cli.delete_workspace import delete_workspace_cmd  # noqa: E402
app.command(name="delete-workspace")(delete_workspace_cmd)


# ``create-project`` / ``update-project`` write to the ``Project`` table
# through the drop-request protocol. No ``delete-project`` by design —
# a Project row is bound to its sessions; projects are archived,
# never deleted.
from twicc.cli.create_project import create_project_cmd  # noqa: E402
app.command(name="create-project")(create_project_cmd)

from twicc.cli.update_project import update_project_cmd  # noqa: E402
app.command(name="update-project")(update_project_cmd)


# ``info`` is a single read-only command taking zero or more positional
# section names (presets, commands, models, agent-settings). The command
# performs lazy Django setup inside its body, so importing it here stays
# cheap.
from twicc.cli.info.command import info_cmd  # noqa: E402
app.command(name="info")(info_cmd)


def main() -> None:
    """Entry point for ``pyproject.toml`` scripts and ``__main__.py``."""
    app()
