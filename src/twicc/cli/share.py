"""``twicc share list`` / ``show`` — read-only, direct DB (works with the server
down). Prints full URLs from the shareBaseUrl synced setting; when it is unset,
prints the relative ``/share/<token>/`` path with a note (sharing has no configured
host — links only resolve on the dedicated share origin, §12)."""

from twicc.cli._output import emit_error, emit_json


def _base_url() -> str:
    from twicc.synced_settings import read_synced_settings
    return (read_synced_settings().get("shareBaseUrl") or "").strip().rstrip("/")


def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int = 50, offset: int = 0) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share

    qs = Share.objects.select_related("session", "artifact_bookmark").all()
    if kind is not None:
        qs = qs.filter(kind=kind)
    if session is not None:
        qs = qs.filter(session_id=session)
    if project is not None:
        from twicc.projects import project_scope_ids
        qs = qs.filter(session__project_id__in=project_scope_ids(project))
    rows = list(qs[offset:offset + limit])
    base = _base_url()
    out = []
    for s in rows:
        if include_revoked or s.status() != "revoked":
            data = serialize_share(s)
            data["url"] = (base + data["url_path"]) if base else data["url_path"]
            out.append(data)
    emit_json(out)


def show_main(share_id: str) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share

    s = Share.objects.select_related("session", "artifact_bookmark").filter(id=share_id).first()
    if s is None:
        emit_error(f"Error: share {share_id!r} not found.", code=1)
    data = serialize_share(s)
    base = _base_url()
    data["url"] = (base + data["url_path"]) if base else data["url_path"]
    emit_json(data)
