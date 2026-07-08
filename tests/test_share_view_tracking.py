import pytest
from django.utils import timezone as djtz

from twicc.core.models import Project, Session, SessionType, Share, ShareAccess
from twicc.core.services.share_tokens import mint_token
from twicc.share import view_tracking


@pytest.fixture
def share(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-vt", directory="/tmp/vt")
    sess = Session.objects.create(
        id="sess-vt", project=project, provider="claude_code",
        file_path="sess-vt.jsonl", type=SessionType.SESSION, title="VT",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )
    return Share.objects.create(kind="session", token=mint_token(), session=sess)


def test_persist_increments_counters_and_rows(share):
    snapshot = {share.id: [
        ("2026-01-01T00:00:00+00:00", "1.2.3.4", "UA1"),
        ("2026-01-01T00:01:00+00:00", "5.6.7.8", "UA2"),
    ]}
    updated = view_tracking._persist(snapshot)
    assert updated == [share.id]
    share.refresh_from_db()
    assert share.view_count == 2
    assert share.last_viewed_at is not None
    assert ShareAccess.objects.filter(share=share).count() == 2


def test_persist_accumulates_across_flushes(share):
    view_tracking._persist({share.id: [("2026-01-01T00:00:00+00:00", "1.1.1.1", "UA")]})
    view_tracking._persist({share.id: [("2026-01-01T00:05:00+00:00", "2.2.2.2", "UA")]})
    share.refresh_from_db()
    assert share.view_count == 2


def test_persist_prunes_to_max_even_with_tied_timestamps(share, monkeypatch):
    # All rows in one flush share the same auto_now_add ``at`` — the prune must still
    # keep exactly _MAX_ACCESS_ROWS (the timestamp-cutoff bug would delete them all).
    monkeypatch.setattr(view_tracking, "_MAX_ACCESS_ROWS", 3)
    snapshot = {share.id: [(f"2026-01-01T00:0{i}:00+00:00", "1.1.1.1", "UA") for i in range(6)]}
    view_tracking._persist(snapshot)
    assert ShareAccess.objects.filter(share=share).count() == 3
    share.refresh_from_db()
    assert share.view_count == 6  # counter is not pruned, only the access log


def test_persist_skips_unknown_share(transactional_db):
    # No DB row for the id → skipped, no crash, nothing reported as updated.
    assert view_tracking._persist({"shr_nope": [("2026-01-01T00:00:00+00:00", "1.1.1.1", "UA")]}) == []
