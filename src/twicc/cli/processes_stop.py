"""``twicc processes stop <SESSION_ID>...`` sub-command.

Batch-stops live agent processes by dropping one ``kind="process:stop"``
request per ``session_id`` in ``<data_dir>/drop-requests/``. Server-side,
each request is dispatched to
:func:`twicc.core.services.process_kill.kill_session_process_from_payload`
exactly like the singular ``process <ID> stop`` (with ``reason="manual"``),
so the operation is fully idempotent — asking to stop a session whose
process is already gone still produces ``status="stopped"``.

Output is a JSON array, one entry per input session_id, in input order
(duplicates collapsed, first occurrence wins). Each entry carries:

- ``status``: ``"stopped"`` / ``"rejected"`` / ``"failed"`` / ``"timeout"``
  for entries that reached the server, or ``"skipped_*"`` for entries
  rejected by the local pre-check (no drop emitted)
- ``session_known``: ``true`` if TwiCC has at least one trace of the
  session (``Session`` row OR any ``ProcessRun`` for this TwiCC)
- ``request_uuid``: the uuid of the drop file, or ``null`` for skipped
- ``provider`` / ``session_title`` / ``project_id``: from the ``Session``
  row when one exists, else ``null``
- ``error``: failure reason for ``rejected`` / ``failed`` / ``skipped_*``
  / ``timeout``; ``null`` for ``stopped``

Exit codes:

- 0 — command ran to completion (inspect each entry's ``status`` for the
  per-id outcome)
- 1 — local CLI validation error (``--timeout`` <= 0, etc.)
- 2 — TwiCC server is not running
- 64 — bad CLI usage (handled by Typer)

A single ``--timeout`` (default 30 s) bounds the entire batch: all drops
are submitted upfront, then we poll every status file in a single loop
until each has a final response or the deadline elapses. Drops are
processed in parallel by the server-side watcher, so a 30 s batch
timeout typically covers N drops without compounding.
"""

from __future__ import annotations

import time

from twicc.cli._output import emit_error, emit_json


POLL_INTERVAL_SECONDS = 0.1

# Map :class:`SessionLookupError.code` → CLI ``status`` value. The mapping
# is exhaustive against the codes raised by ``lookup_session``: any
# unmapped code falls back to ``"skipped_unknown"`` defensively, but a
# new code added there should be reflected here.
_LOOKUP_CODE_TO_SKIP_STATUS = {
    "session_not_found": "skipped_unknown",
    "is_subagent": "skipped_subagent",
    "session_stale": "skipped_stale",
    "project_no_directory": "skipped_no_directory",
    "unknown_provider": "skipped_unknown_provider",
}


def stop_cmd(
    session_ids: list[str],
    *,
    timeout: int,
    force: bool = False,
    spawned_by: str | None = None,
    descendants: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """Batch-stop live agent processes for one or more sessions."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.cli._twicc_info import resolve_live_twicc
    from twicc.core.models import ProcessRun, Session

    # --- Argument validation ---------------------------------------------

    if timeout <= 0:
        emit_error(
            f"Error: --timeout must be > 0 (got {timeout}).",
            code=1,
        )

    if spawned_by == "parent" or descendants == "parent":
        emit_error(
            "Error: processes stop does not support parent-scoped filters. "
            "Use 'self' or an explicit session_id.",
            code=1,
        )

    filiation_scope = any((spawned_by, descendants))
    if annotation and not filiation_scope:
        emit_error(
            "Error: --annotation on processes stop requires --spawned-by "
            "or --descendants.",
            code=1,
        )

    has_scope = filiation_scope or bool(annotation)
    if not session_ids and not has_scope:
        emit_error(
            "Error: no session_ids or filters given. Pass at least one "
            "session_id, or select sessions with --spawned-by or "
            "--descendants. Use --annotation only to narrow that scope.",
            code=1,
        )

    # --- Server-up check (exit 2 mirrors process stop) -------------------

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    info = resolve_live_twicc()
    if info is None:
        emit_error(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            code=2,
        )

    # --- Resolve explicit ids + optional scope filters -------------------

    try:
        from twicc.cli._session_scope import merge_session_scope_ids

        unique_ids = merge_session_scope_ids(
            session_ids,
            spawned_by=spawned_by,
            descendants=descendants,
            annotation=annotation,
        )
    except RuntimeError as e:
        emit_error(f"Error: {e}", code=1)
    except ValueError as e:
        emit_error(f"Error: {e}", code=2)

    if not unique_ids:
        emit_json([])
        return

    # --- Batch metadata fetch (one query each) ---------------------------

    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=unique_ids).only(
            "id", "title", "project_id", "provider"
        )
    }
    # session_known also fires when only a ProcessRun exists (rare:
    # brand-new session not yet seen by the JSONL watcher). It does NOT
    # bypass the lookup_session pre-check — the entry will still be
    # ``skipped_unknown`` if Session is missing — but it documents in
    # the output that TwiCC has *some* trace of the id.
    process_sids = set(
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id__in=unique_ids)
        .values_list("session_id", flat=True)
    )

    # --- Per-id pre-check + drop -----------------------------------------

    # ``outcomes`` is keyed by sid so the final array can be rebuilt in
    # ``unique_ids`` order without searching. ``initial_drops`` holds the
    # (drop, status_path) for every entry that survived the pre-check; we
    # use it as the cleanup source-of-truth so the ``finally`` doesn't
    # have to reason about which entries are still pending.
    outcomes: dict[str, dict] = {}
    initial_drops: list[tuple[str, object, object]] = []  # (sid, drop, status_path)

    for sid in unique_ids:
        session = sessions_by_id.get(sid)
        session_known = session is not None or sid in process_sids
        entry = {
            "session_id": sid,
            "session_known": session_known,
            "status": None,
            "request_uuid": None,
            "provider": session.provider if session is not None else None,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "error": None,
        }
        outcomes[sid] = entry

        try:
            resolved = lookup_session(sid)
        except SessionLookupError as e:
            entry["status"] = _LOOKUP_CODE_TO_SKIP_STATUS.get(
                e.code, "skipped_unknown"
            )
            entry["error"] = e.message
            continue

        payload = {"session_id": resolved.session_id}
        if force:
            payload["force"] = True
        sub = transport.submit(payload, kind="process:stop")
        entry["request_uuid"] = sub.request_uuid
        initial_drops.append((sid, sub))

    # --- Cumulative poll until all pending are resolved or timeout -------

    pending = list(initial_drops)
    deadline = time.time() + timeout
    try:
        while pending and time.time() < deadline:
            still_pending = []
            for sid, sub in pending:
                outcome = sub.poll()
                if outcome is None:
                    # Missing, mid-rename, or "received" — keep polling.
                    still_pending.append((sid, sub))
                    continue
                status = outcome.status
                data = outcome.data
                entry = outcomes[sid]
                entry["status"] = status
                if status == "rejected":
                    errors = data.get("errors", [])
                    entry["error"] = (
                        "; ".join(
                            f"{e.get('code')}: {e.get('message')}"
                            for e in errors
                        )
                        if errors else None
                    )
                elif status == "failed":
                    entry["error"] = data.get("error")
            pending = still_pending
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Anything still pending after the deadline = timeout.
        for sid, _ in pending:
            outcomes[sid]["status"] = "timeout"
            outcomes[sid]["error"] = (
                f"No final status within {timeout}s "
                "(server received the request but did not finish in time)"
            )
    finally:
        # Always clean up the drop + status files we created, even if the
        # poll loop raised. ``missing_ok=True`` covers the server having
        # already deleted them.
        for _, sub in initial_drops:
            sub.cleanup()

    # --- Emit JSON array in input order ----------------------------------

    results = [outcomes[sid] for sid in unique_ids]
    emit_json(results)
