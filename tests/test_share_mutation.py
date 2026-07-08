import asyncio
from pathlib import Path

import pytest
from django.utils import timezone as djtz

from twicc import paths
from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType, Share
from twicc.core.services import share_mutation
from twicc.core.services.share_mutation import (
    _validate_artifact_options,
    _validate_session_options,
    snapshot_artifact_share,
)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-shm", directory="/tmp/shm")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-shm", project=project, provider="claude_code",
        file_path="sess-shm.jsonl", type=SessionType.SESSION, title="Session SHM",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=17,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock",
        _passthrough,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _write(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


# ── Option validation ───────────────────────────────────────────────────────

def test_session_options_rejects_unknown_key():
    _out, errors = _validate_session_options({"bogus": 1})
    assert any(e.code == "unknown_keys" for e in errors)


def test_session_options_rejects_invalid_mode():
    _out, errors = _validate_session_options({"mode": "weird"})
    assert any(e.field == "mode" and e.code == "invalid" for e in errors)


def test_session_options_rejects_invalid_display_mode():
    _out, errors = _validate_session_options({"max_display_mode": "ultra"})
    assert any(e.field == "max_display_mode" for e in errors)


def test_session_options_display_title_trimmed():
    out, errors = _validate_session_options({"display_title": "  Hello  "})
    assert not errors
    assert out["display_title"] == "Hello"


def test_session_options_blank_display_title_dropped():
    out, _errors = _validate_session_options({"display_title": "   "})
    assert "display_title" not in out


def test_artifact_options_rejects_unknown_key():
    _out, errors = _validate_artifact_options({"mode": "live"})
    assert any(e.code == "unknown_keys" for e in errors)


def test_artifact_options_keeps_display_title_and_show_title():
    out, errors = _validate_artifact_options({"display_title": "Report"})
    assert not errors
    # show_title defaults to True (master switch, mirrors sessions).
    assert out == {"show_title": True, "display_title": "Report"}


def test_artifact_options_show_title_false_kept():
    out, errors = _validate_artifact_options({"show_title": False, "display_title": "Report"})
    assert not errors
    assert out == {"show_title": False, "display_title": "Report"}


# ── create_share ────────────────────────────────────────────────────────────

def test_create_session_share_snapshot_freezes_line(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert share.options["mode"] == "snapshot"
    assert share.options["frozen_at_line"] == 17  # session.last_line


def test_create_session_share_live_has_no_frozen_line(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "live"}))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert "frozen_at_line" not in share.options


def test_create_session_share_rejects_unknown_option(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"nope": True}))
    assert not result.success
    assert result.errors


# ── Snapshot size cap ───────────────────────────────────────────────────────

def test_snapshot_size_cap(session, bookmark, artifacts_root, monkeypatch):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"0123456789")  # 10 bytes
    monkeypatch.setattr(share_mutation, "_MAX_SNAPSHOT_BYTES", 4)
    share = Share(id="shr_capcap", kind="artifact", token="x" * 20,
                  artifact_bookmark=bookmark)
    err = snapshot_artifact_share(share)
    assert err is not None
    assert "too large" in err


def test_snapshot_ok_under_cap(session, bookmark, artifacts_root):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html></html>")
    share = Share(id="shr_okok", kind="artifact", token="y" * 20,
                  artifact_bookmark=bookmark)
    err = snapshot_artifact_share(share)
    assert err is None
    snap = paths.get_share_snapshot_dir("shr_okok")
    assert (snap / "index.html").is_file()


# ── patch_share / propagate / from_payload (regression for review findings) ──

def test_frozen_at_line_invalid_rejected():
    _out, errors = _validate_session_options({"mode": "snapshot", "frozen_at_line": "x"})
    assert any(e.field == "frozen_at_line" for e in errors)


def test_patch_preserves_frozen_line(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    assert share.options["frozen_at_line"] == 17
    # Re-send options without frozen_at_line → the stored freeze is preserved.
    result = _run(share_mutation.patch_share(share, {"options": {"mode": "snapshot", "show_timestamps": False}}))
    assert result.success
    share.refresh_from_db()
    assert share.options["frozen_at_line"] == 17
    assert share.options["show_timestamps"] is False


def test_patch_live_to_snapshot_freezes(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "live"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    assert "frozen_at_line" not in share.options
    result = _run(share_mutation.patch_share(share, {"options": {"mode": "snapshot"}}))
    assert result.success
    share.refresh_from_db()
    assert share.options["mode"] == "snapshot"
    assert share.options["frozen_at_line"] == 17  # frozen at session.last_line


def test_update_from_payload_string_expires(session):
    created = _run(share_mutation.create_share("session", session=session, options={"mode": "live"}))
    # The drop-request/CLI path sends expires_at as an ISO string (Finding 1 regression):
    # patch_share must coerce it so the broadcast's serialize_share doesn't crash.
    result = _run(share_mutation.update_share_from_payload({
        "share_id": created.share_id,
        "fields": {"label": "renamed", "expires_at": "2999-01-01T00:00:00+00:00"},
    }))
    assert result.success
    share = Share.objects.get(id=created.share_id)
    assert share.label == "renamed"
    assert share.expires_at is not None
    assert share.expires_at.year == 2999


def test_propagate_session_refreezes(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    session.last_line = 99
    session.save(update_fields=["last_line"])
    share = Share.objects.select_related("session").get(id=created.share_id)
    result = _run(share_mutation.propagate_share(share))
    assert result.success
    share.refresh_from_db()
    assert share.options["frozen_at_line"] == 99


def test_propagate_live_share_rejected(session):
    created = _run(share_mutation.create_share("session", session=session, options={"mode": "live"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    result = _run(share_mutation.propagate_share(share))
    assert not result.success
    assert result.errors[0].code == "not_snapshot"


def test_create_artifact_share_and_propagate_resnapshot(session, bookmark, artifacts_root):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html>v1</html>")
    created = _run(share_mutation.create_share("artifact", bookmark=bookmark))
    assert created.success
    share = Share.objects.select_related("artifact_bookmark").get(id=created.share_id)
    snap = paths.get_share_snapshot_dir(share.id) / "index.html"
    assert snap.read_bytes() == b"<html>v1</html>"
    assert share.options.get("snapshot_at")
    # Mutate the source, propagate → re-snapshot over the existing dir (.old swap branch).
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html>v2</html>")
    result = _run(share_mutation.propagate_share(share))
    assert result.success
    share.refresh_from_db()
    assert snap.read_bytes() == b"<html>v2</html>"
    assert share.options.get("snapshot_at")
