"""Shared batch runner for the ``twicc update-sessions <op>`` sub-commands.

Generalises the single-session ``twicc update-session <ID> <op>`` flow to a
set of sessions: resolve the target ids (explicit ids merged with the optional
``--spawned-by`` / ``--descendants`` / ``--annotation`` scope, same union
semantics as ``twicc sessions`` / ``processes stop`` — explicit ids first,
scope-selected ids appended, deduplicated), drop one request per id reusing the
*exact same* ``kind`` + payload shape the singular command would emit, poll
every status file under a single ``--timeout`` wall-clock budget (the watcher
processes the drops in parallel), then emit one aggregated result.

The result is an object keyed by session_id::

    {
      "summary": {"total", "succeeded", "failed", "all_succeeded"},
      "results": {"<session_id>": <per-id outcome>, ...}
    }

Each per-id outcome is exactly what ``update-session`` would have emitted for
that id alone: ``updated`` / ``rejected`` / ``failed`` / ``timeout`` via
:func:`twicc.cli._drop_request.output.build_final`, or ``validation_error``
when the local ``lookup_session`` pre-check fails before any drop. A per-id
failure never fails the batch — only command-level concerns do.

Exit codes:

- 0  — the batch ran and at least one session was updated, OR the resolved id
       set was empty (nothing to do is not a failure)
- 1  — local argument error (bad ``--timeout``, mutually-exclusive scopes,
       ``--annotation`` without a filiation scope, ``parent`` scope, neither
       ids nor scope, or an unresolvable ``self`` / ``parent``)
- 2  — TwiCC server is not running
- 6  — the resolved id set was non-empty but NOT ONE session was updated
- 64 — bad CLI usage (handled by Typer)
"""

from __future__ import annotations

import time
from collections.abc import Callable

import orjson
import typer

from twicc.cli._output import emit_error, emit_json


POLL_INTERVAL_SECONDS = 0.1


def run_batch_update(
    session_ids: list[str],
    *,
    kind: str,
    prepare: Callable[..., dict | list],
    timeout: int,
    spawned_by: str | None = None,
    descendants: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """Apply one update ``kind`` to every resolved session and emit the batch result.

    ``prepare(resolved)`` returns either the per-id drop payload (a dict that
    must include ``session_id``) or a flat ``list`` of ``ValidationError`` when
    this session can't accept the change (e.g. ``settings`` value invalid for
    its provider) — in that case the id gets a per-id ``validation_error`` and
    no drop, while the other sessions proceed. The provider-agnostic ops pass a
    ``prepare`` that always returns a payload.
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import (
        ServerDownError,
        check_heartbeat,
    )
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import build_final
    from twicc.cli._drop_request.polling import PollOutcome
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )

    # --- Argument validation (global, fatal — the "classic" errors) ------

    if timeout <= 0:
        emit_error(f"Error: --timeout must be > 0 (got {timeout}).", code=1)

    if spawned_by == "parent" or descendants == "parent":
        emit_error(
            "Error: update-sessions does not support parent-scoped filters. "
            "Use 'self' or an explicit session_id.",
            code=1,
        )

    if spawned_by is not None and descendants is not None:
        emit_error(
            "Error: --spawned-by and --descendants are mutually exclusive.",
            code=1,
        )

    filiation_scope = any((spawned_by, descendants))
    if annotation and not filiation_scope:
        emit_error(
            "Error: --annotation requires --spawned-by or --descendants.",
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

    # --- Server-up check (exit 2 mirrors the singular command) -----------

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    # --- Resolve 'self' in the explicit id list --------------------------
    # ``merge_session_scope_ids`` only resolves self/parent for the filiation
    # filters, not for explicit ids. Mirror the singular ``update-session
    # self`` ergonomics by resolving it here (deduplication happens in merge).
    if session_ids and "self" in session_ids:
        from twicc.cli._drop_request.whoami import resolve_current_session

        current = resolve_current_session()
        if current is None:
            emit_error(
                "Error: 'self' could not be resolved: no TwiCC session found "
                "in PID ancestry.",
                code=1,
            )
        session_ids = [current.id if s == "self" else s for s in session_ids]

    # --- Resolve explicit ids + optional scope filters -------------------

    try:
        from twicc.cli._session_scope import merge_session_scope_ids

        unique_ids = merge_session_scope_ids(
            session_ids,
            spawned_by=spawned_by,
            descendants=descendants,
            annotation=annotation,
        )
    except (RuntimeError, ValueError) as e:
        emit_error(f"Error: {e}", code=1)

    if not unique_ids:
        emit_json({
            "summary": {
                "total": 0, "succeeded": 0, "failed": 0, "all_succeeded": True,
            },
            "results": {},
        })
        raise typer.Exit(0)

    # --- Per-id pre-check + drop -----------------------------------------

    # ``results`` is keyed by sid so the final object keeps ``unique_ids``
    # order. Lookup failures land here immediately (validation_error, no
    # drop); survivors get a placeholder filled in after polling.
    results: dict[str, dict | None] = {}
    pending: list[tuple[str, object, object]] = []  # (sid, drop, status_path)

    for sid in unique_ids:
        try:
            resolved = lookup_session(sid)
        except SessionLookupError as e:
            results[sid] = {
                "status": "validation_error",
                "errors": [{
                    "field": "SESSION_ID",
                    "code": e.code,
                    "message": e.message,
                }],
            }
            continue

        outcome = prepare(resolved)
        if isinstance(outcome, list):
            # Per-id validation errors (e.g. settings invalid for this provider).
            results[sid] = {
                "status": "validation_error",
                "errors": [e._asdict() for e in outcome],
            }
            continue

        drop = write_drop_file(outcome, kind=kind)
        status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
        results[sid] = None
        pending.append((sid, drop, status_path))

    # --- Cumulative poll until all pending are resolved or timeout -------

    received_seen: dict[str, bool] = {sid: False for sid, _, _ in pending}
    last_data: dict[str, dict | None] = {sid: None for sid, _, _ in pending}
    still = list(pending)
    deadline = time.time() + timeout
    try:
        while still and time.time() < deadline:
            next_round: list[tuple[str, object, object]] = []
            for sid, drop, status_path in still:
                if not status_path.exists():
                    next_round.append((sid, drop, status_path))
                    continue
                try:
                    data = orjson.loads(status_path.read_bytes())
                except (orjson.JSONDecodeError, OSError):
                    # Status file mid-rename — retry next tick.
                    next_round.append((sid, drop, status_path))
                    continue
                status = data.get("status")
                last_data[sid] = data
                if status == "received":
                    received_seen[sid] = True
                    next_round.append((sid, drop, status_path))
                    continue
                if status in ("updated", "rejected", "failed"):
                    outcome = PollOutcome(
                        status=status, data=data,
                        received_seen=received_seen[sid],
                    )
                    results[sid] = build_final(
                        outcome, request_uuid=drop.request_uuid, timeout=timeout,
                    )
                else:
                    # Unknown intermediate — keep polling.
                    next_round.append((sid, drop, status_path))
            still = next_round
            if still:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Anything still pending after the deadline = timeout.
        for sid, drop, status_path in still:
            outcome = PollOutcome(
                status=None, data=last_data[sid],
                received_seen=received_seen[sid],
            )
            results[sid] = build_final(
                outcome, request_uuid=drop.request_uuid, timeout=timeout,
            )
    finally:
        # Always clean up the drop + status files we created, even on a raise.
        for _, drop, status_path in pending:
            drop.path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)

    # --- Aggregate + emit in input order ---------------------------------

    ordered = {sid: results[sid] for sid in unique_ids}
    total = len(ordered)
    succeeded = sum(1 for v in ordered.values() if v and v.get("status") == "updated")
    failed = total - succeeded

    emit_json({
        "summary": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "all_succeeded": total > 0 and failed == 0,
        },
        "results": ordered,
    })

    # ``total`` is > 0 here (the empty set returned earlier). Zero successes
    # while every argument was valid → distinct exit 6 so a script can detect
    # a total failure without parsing the JSON.
    if succeeded == 0:
        raise typer.Exit(6)
    raise typer.Exit(0)
