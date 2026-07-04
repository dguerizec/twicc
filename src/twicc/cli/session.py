"""CLI implementation for the ``twicc session`` subcommand."""

import orjson

from twicc.cli._output import emit_error, emit_json


def _get_session(session_id: str):
    """Fetch a valid session (created_at set, at least one user message) or exit."""
    from twicc.core.models import Session

    try:
        session = Session.objects.get(
            id=session_id,
            created_at__isnull=False,
            user_message_count__gt=0,
        )
    except Session.DoesNotExist:
        emit_error(f"Error: session '{session_id}' not found.", code=1)

    return session


def _parse_range_filter(range_str: str) -> dict:
    """Parse ``"N"`` or ``"N-M"`` into Django ORM filter kwargs for ``line_num``.

    Exits with status 1 on malformed input.
    """
    if "-" in range_str:
        parts = range_str.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            emit_error(f"Error: invalid range '{range_str}'. Use a number or start-end (e.g. '5' or '10-20').", code=1)
        if start > end:
            emit_error(f"Error: invalid range '{range_str}'. Start must be <= end.", code=1)
        return {"line_num__gte": start, "line_num__lte": end}

    try:
        line_num = int(range_str)
    except ValueError:
        emit_error(f"Error: invalid line number '{range_str}'. Use a number or start-end (e.g. '5' or '10-20').", code=1)
    return {"line_num": line_num}


def _slice_window(seq, total: int, *, limit: int | None, offset: int, tail: int | None):
    """Apply ``tail``/``limit``/``offset`` windowing to a sliceable sequence.

    Works on both a lazy queryset (slicing stays at the DB level) and a plain
    list. ``total`` is only consulted for ``tail`` — callers pass ``qs.count()``
    for a queryset (cheap, avoids materialising it) or ``len(...)`` for a list.
    ``tail`` is assumed already validated as mutually exclusive with
    ``limit``/``offset``.
    """
    if tail is not None:
        return seq[max(0, total - tail):]
    if limit is not None:
        return seq[offset : offset + limit]
    return seq[offset:]


def main(session_id: str) -> None:
    """Fetch a single session by ID and print its JSON representation to stdout."""
    import django

    django.setup()

    from twicc.core.serializers import serialize_session

    session = _get_session(session_id)
    data = serialize_session(session)

    emit_json(data)


def content(session_id: str, *, range_str: str | None = None, contains: list[str] | None = None) -> None:
    """Fetch session item(s) by line/range and/or content substring(s), print as JSON to stdout.

    ``range_str`` and ``contains`` are both optional but at least one must be
    given; when both are present they are combined (the line/range scopes the
    substring search). ``contains`` is a list of case-insensitive substrings
    AND-combined (an item must contain every term) matched against the raw JSONL
    string stored in ``SessionItem.content`` (so it also matches JSON keys and
    sees escaped sequences like ``\\n``).
    """
    import django

    django.setup()

    from twicc.core.models import SessionItem

    contains = contains or []
    if range_str is None and not contains:
        emit_error("Error: provide a line/range argument or --contains.", code=1)

    _get_session(session_id)

    items = SessionItem.objects.filter(session_id=session_id)
    if range_str is not None:
        items = items.filter(**_parse_range_filter(range_str))
    for term in contains:
        # Chained filters AND together; each term must appear in the raw content.
        items = items.filter(content__icontains=term)
    items = items.order_by("line_num")

    # Wrap each item with its line number; parse the raw content string into a real JSON object.
    data = [{"line_num": item.line_num, "content": orjson.loads(item.content)} for item in items]

    if not data:
        emit_error("Error: no items found for the given filter.", code=1)

    emit_json(data)


def messages(
    session_id: str,
    *,
    range_str: str | None = None,
    role: str | None = None,
    contains: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    tail: int | None = None,
) -> None:
    """Fetch user/assistant messages of a session and print as JSON to stdout.

    Output is uniform across providers: a list of
    ``{line_num, text, role, timestamp}`` produced by the same
    ``get_indexable_messages`` helper used by the full-text search
    indexer (the helper's ``from_role`` field is exposed as ``role``
    on the wire — the wider name was an indexing-side concern). Items
    whose provider-specific text extraction yields an empty string are
    silently dropped (same behaviour as the search indexer), so the
    result may contain fewer entries than ``--limit`` or ``--tail``.

    ``contains`` is a list of case-insensitive substrings AND-combined (a
    message must contain every term). Unlike ``content``'s raw-JSONL filter, it
    matches against the extracted ``text`` — what this sub-command emits — so it
    never matches JSON keys or tool noise. Because that text is produced in
    Python, the filter (and, with it, the ``tail``/``limit``/``offset`` window)
    is applied after extraction, on the matching messages.
    """
    import django

    django.setup()

    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.providers.helpers import get_provider_helpers

    session = _get_session(session_id)

    contains = contains or []

    if role is not None and role not in {"user", "assistant"}:
        emit_error(f"Error: invalid --role '{role}'. Use 'user' or 'assistant'.", code=1)

    if tail is not None:
        if limit is not None or offset:
            emit_error("Error: --tail is mutually exclusive with --limit and --offset.", code=1)
        if tail <= 0:
            emit_error(f"Error: --tail must be a positive integer (got {tail}).", code=1)

    if role == "user":
        kinds = [ItemKind.USER_MESSAGE]
    elif role == "assistant":
        kinds = [ItemKind.ASSISTANT_MESSAGE]
    else:
        kinds = [ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE]

    filter_kwargs: dict = {"session_id": session_id, "kind__in": kinds}
    if range_str is not None:
        filter_kwargs |= _parse_range_filter(range_str)

    qs = SessionItem.objects.filter(**filter_kwargs).order_by("line_num")
    helpers = get_provider_helpers(session.provider)

    if contains:
        # The substring filter targets the extracted text, so we materialise and
        # extract everything in range first, filter, then window on the matches.
        terms = [term.lower() for term in contains]
        matched = [
            msg
            for msg in helpers.get_indexable_messages(list(qs))
            if all(term in msg.text.lower() for term in terms)
        ]
        selected = _slice_window(matched, len(matched), limit=limit, offset=offset, tail=tail)
    else:
        # No text filter: window at the DB level, then extract (existing behaviour —
        # the window counts raw items, so dropped-empty extractions may shrink the result).
        total = qs.count() if tail is not None else 0
        items = list(_slice_window(qs, total, limit=limit, offset=offset, tail=tail))
        selected = list(helpers.get_indexable_messages(items))

    data = [
        {
            "line_num": msg.line_num,
            "text": msg.text,
            "role": msg.from_role,
            "timestamp": msg.timestamp,
        }
        for msg in selected
    ]

    emit_json(data)


def agents(session_id: str, *, limit: int = 20, offset: int = 0) -> None:
    """List subagents of a session as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    session = _get_session(session_id)

    if session.parent_session_id is not None:
        emit_error(f"Error: session '{session_id}' is a subagent, not a parent session.", code=1)

    subagents = Session.objects.filter(parent_session_id=session_id).order_by("-mtime")[offset : offset + limit]
    data = [serialize_session(s) for s in subagents]

    emit_json(data)


def plan(session_id: str, *, list_docs: bool = False, doc_path: str | None = None) -> None:
    """Read the session's tracked plan documents (``Session.plan_paths``); print JSON to stdout.

    Three modes:

    - default (no argument): the content of the most recently updated tracked
      document — not necessarily the native Claude plan.
    - a positional path: the content of that document, matched against the
      stored ``path`` (project-relative when the doc lives under the project)
      or its resolved absolute path — never an arbitrary filesystem path.
    - ``--list``: every tracked document, newest first, each entry enriched
      with its resolved ``abs_path`` and fresh ``exists`` (the same entries
      the default session view carries in ``plan_paths``, minus ``abs_path``).
    """
    import os

    import django

    django.setup()

    if list_docs and doc_path:
        emit_error("Error: --list and a positional path are mutually exclusive.", code=1)

    from twicc.providers.plan_docs import resolve_stored_path

    session = _get_session(session_id)

    # Same roots as the compute pipeline's relativization: the session's
    # project directory, then the worktree_of parent's.
    project = session.project
    parent = project.worktree_of if project else None
    roots = [project.directory if project else None, parent.directory if parent else None]

    entries = sorted(
        session.plan_paths or [],
        key=lambda e: e.get("updated_at") or "",
        reverse=True,
    )
    enriched = []
    for entry in entries:
        abs_path = resolve_stored_path(entry.get("path", ""), roots)
        enriched.append({
            **entry,
            "abs_path": abs_path,
            "exists": bool(abs_path and os.path.exists(abs_path)),
        })

    if list_docs:
        emit_json({"plan_paths": enriched})
        return

    if not enriched:
        emit_error(f"Error: no plan documents tracked for session '{session_id}'.", code=1)

    if doc_path:
        requested = os.path.normpath(doc_path)
        entry = next(
            (e for e in enriched if requested in (os.path.normpath(e["path"]), e["abs_path"])),
            None,
        )
        if entry is None:
            emit_error(
                f"Error: no tracked plan document '{doc_path}' for session "
                f"'{session_id}' (see --list for the tracked paths).",
                code=1,
            )
    else:
        entry = enriched[0]

    if not entry["abs_path"]:
        emit_error(f"Error: cannot resolve '{entry['path']}' to an absolute path.", code=1)
    try:
        content = open(entry["abs_path"], encoding="utf-8").read()
    except OSError:
        emit_error(f"Error: plan document '{entry['path']}' is missing on disk.", code=1)
    emit_json({"path": entry["path"], "abs_path": entry["abs_path"], "content": content})


def _workflow_envelope(run, session_cutoff=None) -> dict:
    """The run's parsed ``raw_json`` with its ``runId`` key renamed ``id`` (first).

    An orphaned synthetic run (its session restarted or stopped before any
    ``wf_*.json`` landed) is surfaced as ``interrupted`` at read time — derived,
    not stored. ``session_cutoff`` is ``Session.cutoff`` (its lifecycle boundary).
    """
    from twicc.providers.claude_code.workflow_synthesis import apply_orphan_status

    try:
        raw = orjson.loads(run.raw_json)
    except orjson.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        return {"id": run.run_id, "raw": raw}
    apply_orphan_status(raw, run.updated_at, session_cutoff)
    return {"id": raw.pop("runId", run.run_id), **raw}


def workflows(session_id: str, *, limit: int = 20, offset: int = 0) -> None:
    """List a session's workflows as JSON to stdout (newest first)."""
    import django

    django.setup()

    from twicc.core.models import Workflow

    session = _get_session(session_id)

    runs = Workflow.objects.filter(session_id=session_id).order_by("-updated_at")[offset : offset + limit]
    data = [_workflow_envelope(w, session.cutoff) for w in runs]

    emit_json(data)


def workflow(session_id: str, workflow_id: str) -> None:
    """Show one of a session's workflows as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import Workflow

    session = _get_session(session_id)

    try:
        run = Workflow.objects.get(run_id=workflow_id, session_id=session_id)
    except Workflow.DoesNotExist:
        emit_error(f"Error: workflow '{workflow_id}' not found for session '{session_id}'.", code=1)

    emit_json(_workflow_envelope(run, session.cutoff))
