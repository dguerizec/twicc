"""``twicc processes wait <ITEM>... --timeout N [--all|--first] [--transition]``

Block until one or all session_ids of the input pool reach a virtual
state listed in the requested statuses.

Items are a single positional list, auto-discriminated:

- Any item whose value is in
  :data:`twicc.cli._process_state.VALID_VIRTUAL_STATES`
  (``starting``, ``assistant_turn``, ``awaiting_user_input``,
  ``user_turn``, ``dead``) is treated as a status.
- Everything else is treated as a session_id.

By convention, callers pass session_ids first and statuses last
(documented in the skill). Internally the order is irrelevant — the
discriminator is value-based.

Session ids may also come from scope filters (``--spawned-by`` and
``--descendants``). ``--annotation`` can narrow one of those scopes but
cannot select sessions by itself. Explicit ids and filtered ids are merged,
explicit ids first.

Flags:

- ``--all`` (default) — wait until **every** active session_id has
  matched at least one status; ``--first`` — stop as soon as **one**
  has matched.
- ``--transition`` — only evaluate a match after the row has changed
  since the initial snapshot (mirrors ``process wait``); applied
  per-session.

Tolerant to unknown session_ids: those with no ``Session`` row AND no
``ProcessRun`` for this TwiCC are reported with ``wait_status="skipped_unknown"``
and do NOT participate in either ``--all`` or ``--first``. If every
session_id is skipped the wait pool is empty and the command exits 0
(vacuous truth — there's nothing to wait for).

Exit codes:

- 0 — condition satisfied (``--all`` met or ``--first`` triggered, or
  the active pool was empty)
- 1 — local CLI validation error (timeout <= 0, no statuses passed,
  no session_ids passed)
- 2 — TwiCC server is not running (or has been restarted under us)
- 5 — timeout: at least one active session_id never matched (``--all``)
  or no session_id matched at all (``--first``)
- 64 — bad CLI usage (handled by Typer)
"""

from __future__ import annotations

import time

import typer

from twicc.cli._output import emit_json


POLL_INTERVAL_SECONDS = 0.25


def wait_cmd(
    items: list[str],
    *,
    timeout: float,
    wait_all: bool,
    transition: bool,
    spawned_by: str | None = None,
    descendants: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """Block until the input pool of session_ids matches the requested statuses."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.agent.states import AgentState
    from twicc.cli._drop_request.discovery import (
        ServerDownError,
        check_heartbeat,
    )
    from twicc.cli._process_state import (
        VALID_VIRTUAL_STATES,
        project_virtual_state,
    )
    from twicc.cli._twicc_info import resolve_live_twicc
    from twicc.core.models import ProcessRun, Session

    # --- Argument validation ---------------------------------------------

    if timeout <= 0:
        typer.echo(
            f"Error: --timeout must be > 0 (got {timeout}).",
            err=True,
        )
        raise typer.Exit(1)

    # Split items by value. Order is preserved for each kind (so error
    # messages and the JSON output stay deterministic), duplicates are
    # collapsed (first occurrence wins).
    requested_set: set[str] = set()
    statuses_ordered: list[str] = []
    session_ids_seen: set[str] = set()
    session_ids_ordered: list[str] = []
    for item in items:
        if item in VALID_VIRTUAL_STATES:
            if item not in requested_set:
                requested_set.add(item)
                statuses_ordered.append(item)
        else:
            if item not in session_ids_seen:
                session_ids_seen.add(item)
                session_ids_ordered.append(item)

    if not statuses_ordered:
        typer.echo(
            "Error: no statuses given (expected at least one of: "
            f"{', '.join(sorted(VALID_VIRTUAL_STATES))}).",
            err=True,
        )
        raise typer.Exit(1)

    if spawned_by == "parent" or descendants == "parent":
        typer.echo(
            "Error: processes wait does not support parent-scoped filters. "
            "Use 'self' or an explicit session_id.",
            err=True,
        )
        raise typer.Exit(1)

    filiation_scope = any((spawned_by, descendants))
    if annotation and not filiation_scope:
        typer.echo(
            "Error: --annotation on processes wait requires --spawned-by "
            "or --descendants.",
            err=True,
        )
        raise typer.Exit(1)

    has_scope = filiation_scope or bool(annotation)
    if not session_ids_ordered and not has_scope:
        typer.echo(
            "Error: no session_ids or filters given. Pass at least one "
            "session_id, or select sessions with --spawned-by or "
            "--descendants. Use --annotation only to narrow that scope.",
            err=True,
        )
        raise typer.Exit(1)

    # --- Server-up check (exit 2) ----------------------------------------

    try:
        check_heartbeat()
    except ServerDownError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)

    info = resolve_live_twicc()
    if info is None:
        typer.echo(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            err=True,
        )
        raise typer.Exit(2)

    # --- Resolve optional scope filters ---------------------------------

    try:
        from twicc.cli._session_scope import merge_session_scope_ids

        session_ids_ordered = merge_session_scope_ids(
            session_ids_ordered,
            spawned_by=spawned_by,
            descendants=descendants,
            annotation=annotation,
        )
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)

    if not session_ids_ordered:
        emit_json([])
        raise typer.Exit(0)

    # --- Batch metadata fetch + session_known ----------------------------

    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=session_ids_ordered).only(
            "id", "title", "project_id", "provider"
        )
    }
    process_sids = set(
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id__in=session_ids_ordered)
        .values_list("session_id", flat=True)
    )

    # outcomes is keyed by sid so the final array can be rebuilt in input
    # order. active_ids is the subset that participates in the wait pool
    # (everything else is skipped_unknown).
    outcomes: dict[str, dict] = {}
    active_ids: list[str] = []
    for sid in session_ids_ordered:
        session = sessions_by_id.get(sid)
        session_known = session is not None or sid in process_sids
        outcomes[sid] = {
            "session_id": sid,
            "session_known": session_known,
            "wait_status": None,  # filled below
            "matched_state": None,
            "current_state": None,
            "provider": session.provider if session is not None else None,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
        }
        if session_known:
            active_ids.append(sid)
        else:
            outcomes[sid]["wait_status"] = "skipped_unknown"
            # Vacuous projection — there's no row to read.
            outcomes[sid]["current_state"] = "dead"

    def emit_and_exit(exit_code: int) -> None:
        """Serialize ``outcomes`` in input order and raise ``typer.Exit``."""
        results = [outcomes[sid] for sid in session_ids_ordered]
        emit_json(results)
        raise typer.Exit(exit_code)

    # --- Vacuous truth: every session_id was skipped ---------------------

    if not active_ids:
        emit_and_exit(0)

    # --- Batched row fetch helpers --------------------------------------

    def latest_rows_for(sids: list[str]) -> dict[str, object]:
        """Most-recent ``ProcessRun`` per session_id, in one query.

        DEAD rows are NOT excluded: ``wait`` needs to observe the
        ``"dead"`` virtual state for sessions whose process has died.
        Missing sids map to ``None`` (no row at all).
        """
        rows: dict[str, object] = {sid: None for sid in sids}
        for row in (
            ProcessRun.objects
            .filter(twicc_pid=info.pid, session_id__in=sids)
            .order_by("session_id", "-started_at")
        ):
            if rows.get(row.session_id) is None:
                rows[row.session_id] = row
        return rows

    def row_change_marker(row) -> tuple:
        """Stable marker that flips on every transition or presence change."""
        if row is None:
            return (None, False)
        is_live = row.state != AgentState.DEAD.value
        return (row.last_state_change_at, is_live)

    # --- Initial snapshot + transition markers --------------------------

    initial_rows = latest_rows_for(active_ids)
    initial_markers: dict[str, tuple] = {}
    pending: list[str] = []

    for sid in active_ids:
        row = initial_rows[sid]
        initial_markers[sid] = row_change_marker(row)
        current = project_virtual_state(row)
        outcomes[sid]["current_state"] = current

        # In normal mode, an initial match counts. In --transition mode
        # we always wait for at least one observed change first.
        if not transition and current in requested_set:
            outcomes[sid]["wait_status"] = "matched"
            outcomes[sid]["matched_state"] = current
        else:
            outcomes[sid]["wait_status"] = "pending"
            pending.append(sid)

    # Short-circuit if the initial snapshot already satisfies the goal.
    if not wait_all and any(
        outcomes[sid]["wait_status"] == "matched" for sid in active_ids
    ):
        emit_and_exit(0)
    if wait_all and not pending:
        emit_and_exit(0)

    # --- Poll loop -------------------------------------------------------

    deadline = time.monotonic() + timeout
    timed_out = False

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)

        # Re-verify the server hasn't died or been restarted under us.
        live_info = resolve_live_twicc()
        if live_info is None or live_info.pid != info.pid:
            typer.echo(
                "Error: TwiCC server is no longer running "
                "(twicc.info.json missing or PID changed).",
                err=True,
            )
            raise typer.Exit(2)

        rows = latest_rows_for(pending)
        still_pending: list[str] = []
        for sid in pending:
            row = rows[sid]
            marker = row_change_marker(row)
            current = project_virtual_state(row)
            outcomes[sid]["current_state"] = current

            # --transition: defer evaluation until something has changed.
            if transition and marker == initial_markers[sid]:
                still_pending.append(sid)
                continue

            if current in requested_set:
                outcomes[sid]["wait_status"] = "matched"
                outcomes[sid]["matched_state"] = current
            else:
                still_pending.append(sid)

        pending = still_pending

        if not wait_all and any(
            outcomes[sid]["wait_status"] == "matched" for sid in active_ids
        ):
            break
        if wait_all and not pending:
            break

        if time.monotonic() >= deadline:
            timed_out = True
            break

    emit_and_exit(5 if timed_out else 0)
