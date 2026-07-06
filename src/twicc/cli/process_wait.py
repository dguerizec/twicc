"""``twicc process <ID> wait <STATUS>... --timeout N`` sub-command.

Polls ``ProcessRun`` for the given session until its virtual state matches
one of the requested statuses (or timeout). Pure local observation: no
server request, no Channels subscription. The live TwiCC writes
transitions to the same table this command reads.

Server-down handling follows the ``stop`` pattern (``check_heartbeat`` →
exit 2), distinct from validation errors (exit 1) and bad CLI usage
(exit 64). The full design lives in
``docs/superpowers/specs/2026-05-30-process-wait-subcommand-design.md``.

On a match the result is emitted as JSON on stdout (exit 0). A timeout
prints a short reason on stderr and exits 5 — no stdout payload.
"""

from __future__ import annotations

import time

import typer

from twicc.cli._output import emit_error, emit_json


POLL_INTERVAL_SECONDS = 0.25


def wait_cmd(
    session_id: str,
    statuses: list[str],
    *,
    timeout: float,
    transition: bool,
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
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._twicc_info import resolve_live_twicc
    from twicc.core.models import ProcessRun, Session

    # --- Argument validation ---------------------------------------------

    if timeout <= 0:
        emit_error(
            f"Error: --timeout must be > 0 (got {timeout}).",
            code=64,
        )

    if not statuses:
        # typer.Argument(...) already enforces this; keep the explicit
        # guard so the error message matches the rest of the validation.
        emit_error("Error: at least one status is required.", code=64)

    requested = []
    seen: set[str] = set()
    for s in statuses:
        if s in seen:
            continue
        if s not in VALID_VIRTUAL_STATES:
            emit_error(
                f"Error: invalid status '{s}'. Use one of: "
                f"{', '.join(sorted(VALID_VIRTUAL_STATES))}.",
                code=64,
            )
        requested.append(s)
        seen.add(s)
    requested_set = frozenset(requested)

    # --- Server-up check (exit 2 on failure, like stop_cmd) --------------

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    info = resolve_live_twicc()
    if info is None:
        # Heartbeat passed but info.json is missing or its PID is dead.
        # Treat as server down to keep the exit code coherent.
        emit_error(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            code=2,
        )

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
        emit_error(
            f"Error: unknown session '{session_id}' "
            f"(no Session row and no ProcessRun in this TwiCC).",
            code=1,
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

    def emit_match(row, matched_state):
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
        emit_json(data)

    # Normal mode: if the initial state already matches, return immediately.
    if not transition and initial_virtual in requested_set:
        emit_match(initial_row, initial_virtual)
        raise typer.Exit(0)

    deadline = time.monotonic() + timeout

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)

        # Re-verify the server hasn't died (or been restarted under us)
        # between polls. If twicc.info.json now points at a different PID,
        # any row we read tagged with the old pid is meaningless.
        live_info = resolve_live_twicc()
        if live_info is None or live_info.pid != info.pid:
            emit_error(
                "Error: TwiCC server is no longer running "
                "(twicc.info.json missing or PID changed).",
                code=2,
            )

        row = latest_row()
        marker = row_change_marker(row)
        virtual = project_virtual_state(row)

        if transition and marker == initial_marker:
            # No transition observed yet — keep polling without evaluating.
            if time.monotonic() >= deadline:
                emit_error(
                    f"Timeout after {timeout:g}s "
                    f"(no transition observed, state: {virtual}).",
                    code=5,
                )
            continue

        if virtual in requested_set:
            emit_match(row, virtual)
            raise typer.Exit(0)

        if time.monotonic() >= deadline:
            emit_error(
                f"Timeout after {timeout:g}s "
                f"(last observed state: {virtual}).",
                code=5,
            )
