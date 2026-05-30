"""``twicc process <ID> wait <STATUS>... --timeout N`` sub-command.

Polls ``ProcessRun`` for the given session until its virtual state matches
one of the requested statuses (or timeout). Pure local observation: no
server request, no Channels subscription. The live TwiCC writes
transitions to the same table this command reads.

Server-down handling follows the ``stop`` pattern (``check_heartbeat`` →
exit 2), distinct from validation errors (exit 1) and bad CLI usage
(exit 64). The full design lives in
``docs/superpowers/specs/2026-05-30-process-wait-subcommand-design.md``.
"""

from __future__ import annotations

import sys
import time

import orjson
import typer


POLL_INTERVAL_SECONDS = 0.25


def _log(line: str, *, json_output: bool, err: bool = False) -> None:
    """Print a progress line unless JSON output is requested."""
    if not json_output:
        typer.echo(line, err=err)


def wait_cmd(
    session_id: str,
    statuses: list[str],
    *,
    timeout: float,
    transition: bool,
    no_color: bool,  # noqa: ARG001 - accepted for CLI consistency
    json_output: bool,
) -> None:
    """Block until the live process reaches any of the listed virtual states."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.agent.states import AgentState
    from twicc.cli._process_state import (
        VALID_VIRTUAL_STATES,
        project_virtual_state,
        serialize_wait_result,
    )
    from twicc.cli._drop_request.discovery import (
        ServerDownError,
        check_heartbeat,
    )
    from twicc.cli._twicc_info import resolve_live_twicc
    from twicc.core.models import ProcessRun, Session

    # --- Argument validation ---------------------------------------------

    if timeout <= 0:
        typer.echo(
            f"Error: --timeout must be > 0 (got {timeout}).",
            err=True,
        )
        raise typer.Exit(64)

    if not statuses:
        # typer.Argument(...) already enforces this; keep the explicit
        # guard so the error message matches the rest of the validation.
        typer.echo("Error: at least one status is required.", err=True)
        raise typer.Exit(64)

    requested = []
    seen: set[str] = set()
    for s in statuses:
        if s in seen:
            continue
        if s not in VALID_VIRTUAL_STATES:
            typer.echo(
                f"Error: invalid status '{s}'. Use one of: "
                f"{', '.join(sorted(VALID_VIRTUAL_STATES))}.",
                err=True,
            )
            raise typer.Exit(64)
        requested.append(s)
        seen.add(s)
    requested_set = frozenset(requested)

    # --- Server-up check (exit 2 on failure, like stop_cmd) --------------

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)

    info = resolve_live_twicc()
    if info is None:
        # Heartbeat passed but info.json is missing or its PID is dead.
        # Treat as server down to keep the exit code coherent.
        typer.echo(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            err=True,
        )
        raise typer.Exit(2)

    _log(f"✓ Heartbeat OK (last seen {age:.1f}s ago)", json_output=json_output)

    # --- session_id validation -------------------------------------------

    session = (
        Session.objects.filter(id=session_id)
        .only("id", "title", "project_id", "provider")
        .first()
    )
    has_processrun = ProcessRun.objects.filter(
        twicc_pid=info.pid, session_id=session_id
    ).exists()
    if session is None and not has_processrun:
        typer.echo(
            f"Error: unknown session '{session_id}' "
            f"(no Session row and no ProcessRun in this TwiCC).",
            err=True,
        )
        raise typer.Exit(1)

    mode_label = "--transition" if transition else "normal"
    _log(
        f"→ Waiting for: {', '.join(requested)} "
        f"(timeout {timeout:g}s, mode: {mode_label})",
        json_output=json_output,
    )

    # --- Snapshot + poll loop --------------------------------------------

    def latest_row():
        """Most-recent ``ProcessRun`` for ``(twicc_pid, session_id)``.

        DEAD rows are NOT excluded here: ``wait`` needs to observe the
        ``"dead"`` virtual state, which collapses both "row is DEAD" and
        "no row at all" into one matchable value.
        """
        return (
            ProcessRun.objects
            .filter(twicc_pid=info.pid, session_id=session_id)
            .order_by("-started_at")
            .first()
        )

    def row_change_marker(row):
        """Stable marker that flips on every transition or presence change.

        Used by ``--transition`` mode to know whether *something* changed
        since the initial snapshot. A change is either: a different
        ``last_state_change_at``, or a flip between "alive row exists"
        and "no alive row" (None or DEAD).
        """
        if row is None:
            return (None, False)
        is_live = row.state != AgentState.DEAD.value
        return (row.last_state_change_at, is_live)

    initial_row = latest_row()
    initial_marker = row_change_marker(initial_row)
    initial_virtual = project_virtual_state(initial_row)

    def emit_match(row, matched_state, elapsed=None):
        if elapsed is not None:
            _log(
                f"✓ Matched '{matched_state}' after {elapsed:.1f}s",
                json_output=json_output,
            )
        else:
            _log(f"✓ Matched '{matched_state}'", json_output=json_output)

        # Re-fetch the Session row in case the watcher created it (or
        # refreshed the title) while we were polling.
        sess = (
            Session.objects.filter(id=session_id)
            .only("id", "title", "project_id", "provider")
            .first()
        )
        data = serialize_wait_result(
            row, sess, session_id=session_id, matched_state=matched_state
        )
        sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        sys.stdout.buffer.write(b"\n")

    # Normal mode: if the initial state already matches, return immediately.
    if not transition and initial_virtual in requested_set:
        emit_match(initial_row, initial_virtual)
        raise typer.Exit(0)

    if transition:
        _log(
            f"… Initial state: {initial_virtual} "
            f"(waiting for a transition before evaluating match)",
            json_output=json_output,
        )
    else:
        _log(
            f"… Initial state: {initial_virtual} "
            f"(not in requested set; polling)",
            json_output=json_output,
        )

    deadline = time.monotonic() + timeout
    start = time.monotonic()

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)

        # Re-verify the server hasn't died (or been restarted under us)
        # between polls. If twicc.info.json now points at a different PID,
        # any row we read tagged with the old pid is meaningless.
        live_info = resolve_live_twicc()
        if live_info is None or live_info.pid != info.pid:
            typer.echo(
                "Error: TwiCC server is no longer running "
                "(twicc.info.json missing or PID changed).",
                err=True,
            )
            raise typer.Exit(2)

        row = latest_row()
        marker = row_change_marker(row)
        virtual = project_virtual_state(row)

        if transition and marker == initial_marker:
            # No transition observed yet — keep polling without evaluating.
            if time.monotonic() >= deadline:
                _log(
                    f"✗ Timeout after {timeout:g}s "
                    f"(no transition observed, state: {virtual})",
                    json_output=json_output,
                    err=True,
                )
                raise typer.Exit(5)
            continue

        if virtual in requested_set:
            elapsed = time.monotonic() - start
            emit_match(row, virtual, elapsed=elapsed)
            raise typer.Exit(0)

        if time.monotonic() >= deadline:
            _log(
                f"✗ Timeout after {timeout:g}s "
                f"(last observed state: {virtual})",
                json_output=json_output,
                err=True,
            )
            raise typer.Exit(5)
