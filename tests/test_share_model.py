from datetime import datetime, timedelta, timezone

import pytest
from django.db import IntegrityError
from django.utils import timezone as djtz

from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType, Share
from twicc.core.serializers import serialize_share, serialize_share_public_meta
from twicc.core.services.share_tokens import mint_token, resolve_share


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-share", directory="/tmp/share")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-share", project=project, provider="claude_code",
        file_path="sess-share.jsonl", type=SessionType.SESSION, title="My session",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=42,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


def test_check_constraint_rejects_session_kind_without_session(project, bookmark):
    with pytest.raises(IntegrityError):
        Share.objects.create(kind="session", token=mint_token(), session=None,
                             artifact_bookmark=bookmark)


def test_check_constraint_rejects_artifact_kind_with_session(session, bookmark):
    with pytest.raises(IntegrityError):
        Share.objects.create(kind="artifact", token=mint_token(), session=session,
                             artifact_bookmark=bookmark)


def test_valid_session_share_saves(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session)
    assert share.id.startswith("shr_")
    assert share.status() == "active"


def test_resolve_share_unknown_returns_none(transactional_db):
    assert resolve_share("does-not-exist") is None
    assert resolve_share("") is None


def test_resolve_share_found(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session)
    resolved = resolve_share(share.token)
    assert resolved is not None
    assert resolved.id == share.id


def test_status_transitions(session):
    now = datetime.now(tz=timezone.utc)
    active = Share.objects.create(kind="session", token=mint_token(), session=session)
    assert active.status() == "active"
    assert active.is_active()

    expired = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        expires_at=now - timedelta(hours=1),
    )
    assert expired.status() == "expired"
    assert not expired.is_active()

    # Revoked wins over expired.
    revoked = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        expires_at=now - timedelta(hours=1), revoked_at=now,
    )
    assert revoked.status() == "revoked"


def test_serialize_share_session(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session,
                                 label="private label")
    data = serialize_share(share)
    assert data["kind"] == "session"
    assert data["token"] == share.token
    assert data["url_path"] == f"/share/{share.token}/"
    assert data["session_id"] == "sess-share"
    assert data["target_title"] == "My session"
    assert data["label"] == "private label"
    assert data["has_password"] is False


def test_public_meta_hides_title_when_show_title_false(session):
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": False},
    )
    meta = serialize_share_public_meta(share)
    # No real title exposed, and never the label / counters.
    assert "title" not in meta
    assert "label" not in meta
    assert meta["session_id"] == "sess-share"


def test_public_meta_show_title_is_master(session):
    # show_title off ⇒ no title, even if a display_title override is stored.
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": False, "display_title": "Public name"},
    )
    assert "title" not in serialize_share_public_meta(share)


def test_public_meta_display_title_used_when_shown(session):
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": True, "display_title": "Public name"},
    )
    assert serialize_share_public_meta(share)["title"] == "Public name"
