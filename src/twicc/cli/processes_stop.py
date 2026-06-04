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

import orjson
import typer

from twicc.cli._output import emit_json


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
) -> None:
    """Batch-stop live agent processes for one or more sessions."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import (
        ServerDownError,
        check_heartbeat,
    )
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
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

    # --- Server-up check (exit 2 mirrors process stop) -------------------

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

    # --- Dedupe while preserving caller order ----------------------------

    unique_ids: list[str] = []
    seen: set[str] = set()
    for sid in session_ids:
        if sid in seen:
            continue
        seen.add(sid)
        unique_ids.append(sid)

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

        drop = write_drop_file(
            {"session_id": resolved.session_id},
            kind="process:stop",
        )
        status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
        entry["request_uuid"] = drop.request_uuid
        initial_drops.append((sid, drop, status_path))

    # --- Cumulative poll until all pending are resolved or timeout -------

    pending = list(initial_drops)
    deadline = time.time() + timeout
    try:
        while pending and time.time() < deadline:
            still_pending = []
            for sid, drop, status_path in pending:
                if not status_path.exists():
                    still_pending.append((sid, drop, status_path))
                    continue
                try:
                    data = orjson.loads(status_path.read_bytes())
                except (orjson.JSONDecodeError, OSError):
                    # Status file mid-rename — retry next tick.
                    still_pending.append((sid, drop, status_path))
                    continue
                status = data.get("status")
                if status in ("stopped", "rejected", "failed"):
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
                else:
                    # "received" or other intermediate — keep polling.
                    still_pending.append((sid, drop, status_path))
            pending = still_pending
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Anything still pending after the deadline = timeout.
        for sid, _, _ in pending:
            outcomes[sid]["status"] = "timeout"
            outcomes[sid]["error"] = (
                f"No final status within {timeout}s "
                "(server received the request but did not finish in time)"
            )
    finally:
        # Always clean up the drop + status files we created, even if the
        # poll loop raised. ``missing_ok=True`` covers the server having
        # already deleted them.
        for _, drop, status_path in initial_drops:
            drop.path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)

    # --- Emit JSON array in input order ----------------------------------

    results = [outcomes[sid] for sid in unique_ids]
    emit_json(results)
