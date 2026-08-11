"""``twicc share`` (list) / ``show`` — read-only, direct DB (works with the server
down). ``url`` follows the §7.4 parity contract of the agent-sharing design:
byte-identical to the URL the owner UI shows for the same share (mirrored
builder ``core/services/share_url.py`` ↔ ``frontend/src/utils/shareUrlCore.js``).
With ``shareBaseUrl`` unset, prints the relative ``/share/<token>/`` path
(links only resolve on the dedicated share origin)."""

from twicc.cli._output import emit_error, emit_json


def _base_url() -> str:
    from twicc.core.services.share_url import normalize_share_base
    from twicc.synced_settings import read_synced_settings
    return normalize_share_base(read_synced_settings().get("shareBaseUrl"))


def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int = 50, offset: int = 0) -> None:
    import django
    django.setup()

    from django.db.models import Q

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share
    from twicc.core.services.share_url import build_share_url

    qs = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).all()
    if kind is not None:
        qs = qs.filter(kind=kind)
    if session is not None:
        qs = qs.filter(Q(session_id=session) | Q(artifact_bookmark__session_id=session))
    if project is not None:
        from twicc.projects import project_scope_ids
        ids = project_scope_ids(project)
        # Both kinds: an artifact share has session NULL (CheckConstraint), its
        # project comes from the bookmark's denormalised raw project FK.
        qs = qs.filter(Q(session__project_id__in=ids) | Q(artifact_bookmark__project_id__in=ids))
    rows = list(qs[offset:offset + limit])
    base = _base_url()
    out = []
    for s in rows:
        if include_revoked or s.status() != "revoked":
            data = serialize_share(s)
            data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
            out.append(data)
    emit_json(out)


def show_main(share_id: str) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share
    from twicc.core.services.share_url import build_share_url

    s = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).filter(id=share_id).first()
    if s is None:
        emit_error(f"Error: share {share_id!r} not found.", code=1)
    data = serialize_share(s)
    base = _base_url()
    data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
    emit_json(data)
