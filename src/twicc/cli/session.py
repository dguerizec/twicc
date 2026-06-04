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


def main(session_id: str) -> None:
    """Fetch a single session by ID and print its JSON representation to stdout."""
    import django

    django.setup()

    from twicc.core.serializers import serialize_session

    session = _get_session(session_id)
    data = serialize_session(session)

    emit_json(data)


def content(session_id: str, *, range_str: str) -> None:
    """Fetch session item(s) by line number or range and print as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import SessionItem

    _get_session(session_id)

    items = (
        SessionItem.objects
        .filter(session_id=session_id, **_parse_range_filter(range_str))
        .order_by("line_num")
    )

    # Parse each item's content string into real JSON objects
    data = [orjson.loads(item.content) for item in items]

    if not data:
        emit_error("Error: no items found for the given range.", code=1)

    emit_json(data)


def messages(
    session_id: str,
    *,
    range_str: str | None = None,
    role: str | None = None,
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
    """
    import django

    django.setup()

    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.providers.helpers import get_provider_helpers

    session = _get_session(session_id)

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
    if tail is not None:
        count = qs.count()
        items = list(qs[max(0, count - tail):])
    elif limit is not None:
        items = list(qs[offset : offset + limit])
    elif offset:
        items = list(qs[offset:])
    else:
        items = list(qs)

    helpers = get_provider_helpers(session.provider)
    data = [
        {
            "line_num": msg.line_num,
            "text": msg.text,
            "role": msg.from_role,
            "timestamp": msg.timestamp,
        }
        for msg in helpers.get_indexable_messages(items)
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
