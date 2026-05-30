"""``twicc whoami`` — identify the session that owns the calling process."""

from __future__ import annotations

import os
import sys

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
    prints the same details ``twicc session <ID>`` does — title,
    provider, project_id, cost, settings, lifecycle, spawned_by, etc.

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import django

    django.setup()

    from twicc.cli._session_request.whoami import resolve_current_session
    from twicc.core.serializers import serialize_session

    session = resolve_current_session()
    if session is None:
        msg = (
            "No TwiCC session found in PID ancestry. whoami is only "
            "meaningful from inside an active agent session."
        )
        if json_output:
            import orjson

            sys.stdout.buffer.write(orjson.dumps({"error": msg}))
            sys.stdout.write("\n")
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(1)

    data = serialize_session(session)
    import orjson

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
