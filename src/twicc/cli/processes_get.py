"""``twicc processes get <SESSION_ID>...`` sub-command.

Look up the live ``ProcessRun`` state for one or more session_ids and
emit a JSON array — one entry per requested session_id, in the order
they were passed (duplicates collapsed, first occurrence wins).

Unlike ``twicc processes``, ``get`` does NOT take filter flags: when the
caller already names the sessions it cares about, layering
``--provider`` / ``--state`` on top would only blur the meaning of the
placeholder rows. Each entry is therefore one of:

- live process row → projected onto the 5-value virtual state
- ``state="dead"`` placeholder when no live row exists for the
  ``session_id`` in this TwiCC

Every entry carries a ``session_known`` flag:

- ``true`` when TwiCC has at least one trace of the session (a
  ``Session`` row OR any ``ProcessRun`` row, even DEAD)
- ``false`` when neither exists — surfaces typos, periodic cleanup, or
  stale session_ids from a long-lived script

Server-down handling matches ``processes``: ``resolve_live_twicc_or_exit``
prints to stderr and exits 1 if no live TwiCC owns ``twicc.info.json``.
"""

from __future__ import annotations

from twicc.cli._output import emit_json


def main(session_ids: list[str]) -> None:
    """Emit one JSON entry per ``session_id`` (placeholder dead if missing)."""
    import django

    django.setup()

    from twicc.cli._process_state import serialize_get_result
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Session

    info = resolve_live_twicc_or_exit()

    # Deduplicate while preserving caller order: the output mirrors the input
    # 1-to-1 so scripts can zip(ids, output) without re-mapping.
    unique_ids: list[str] = []
    seen: set[str] = set()
    for sid in session_ids:
        if sid in seen:
            continue
        seen.add(sid)
        unique_ids.append(sid)

    # Single batch query for the Session rows we may need (titles, providers,
    # project_ids). Missing entries simply don't appear in the dict.
    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=unique_ids).only(
            "id", "title", "project_id", "provider"
        )
    }

    # Single batch query for ProcessRun rows. DEAD rows are NOT excluded:
    # they participate in ``session_known`` (the session existed for this
    # TwiCC even if the live process is gone). The most-recent row per
    # session_id wins (matches ``process.py`` for the narrow cron-restart
    # window where two rows briefly coexist).
    rows_by_id: dict[str, ProcessRun] = {}
    for row in (
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id__in=unique_ids)
        .order_by("session_id", "-started_at")
    ):
        if row.session_id not in rows_by_id:
            rows_by_id[row.session_id] = row

    results = []
    for sid in unique_ids:
        session = sessions_by_id.get(sid)
        row = rows_by_id.get(sid)
        session_known = session is not None or row is not None
        # serialize_get_result handles the row=None branch (placeholder dead)
        # AND collapses row.state==DEAD to the same "dead" output, so the
        # caller only needs to pass whatever row it found.
        results.append(
            serialize_get_result(
                row,
                session,
                session_id=sid,
                session_known=session_known,
            )
        )

    emit_json(results)
