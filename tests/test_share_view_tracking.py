import asyncio
import time

import pytest
from django.utils import timezone as djtz

from twicc.core.models import ArtifactBookmark, Project, Session, SessionType, Share, ShareAccess
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


# ── Notification copy ───────────────────────────────────────────────────────
# Every test below runs WITHOUT a db fixture: the objects are built in memory and
# pytest-django blocks any query, which is exactly the contract under test — the
# notification path must never lazy-load a relation from the async flush task.

def _session_share(title, label="", options=None):
    session = Session(id="sess-desc", title=title)
    return Share(id="shr_sess", kind="session", token="tok", session=session,
                 label=label, options=options or {})


def _artifact_share(name, label="", relative_path="a/b/index.html", options=None):
    bookmark = ArtifactBookmark(id=1, name=name, relative_path=relative_path)
    return Share(id="shr_art", kind="artifact", token="tok", artifact_bookmark=bookmark,
                 label=label, options=options or {})


@pytest.mark.parametrize(("title", "label", "expected"), [
    ("Parser refactor", "client demo", "session share 'Parser refactor' (client demo)"),
    ("Parser refactor", "", "session share 'Parser refactor'"),
    (None, "client demo", "session share 'client demo'"),
    (None, "", "session share 'shr_sess'"),
    ("  Padded  ", "  spaced  ", "session share 'Padded' (spaced)"),
])
def test_descriptor_session(title, label, expected):
    assert view_tracking._share_descriptor(_session_share(title, label)) == expected


@pytest.mark.parametrize(("name", "label", "expected"), [
    ("Dashboard", "v2", "artifact share 'Dashboard' (v2)"),
    ("Dashboard", "", "artifact share 'Dashboard'"),
    ("", "", "artifact share 'a/b/index.html'"),  # no name ⇒ the relative path
])
def test_descriptor_artifact(name, label, expected):
    assert view_tracking._share_descriptor(_artifact_share(name, label)) == expected


def test_descriptor_real_title_wins_over_display_title():
    # display_title is the owner's PUBLIC alias; the notification goes to the owner,
    # who recognises the real title.
    share = _session_share("Parser refactor", options={"display_title": "Public alias"})
    assert view_tracking._share_descriptor(share) == "session share 'Parser refactor'"


@pytest.mark.parametrize("share_factory", [
    lambda opts: _session_share("", options=opts),
    lambda opts: _artifact_share("", relative_path="", options=opts),
])
def test_descriptor_falls_back_to_display_title(share_factory):
    share = share_factory({"display_title": "Public alias"})
    assert view_tracking._share_descriptor(share).endswith("share 'Public alias'")


def test_descriptor_ignores_show_title():
    # show_title only hides the title from VIEWERS — the owner always sees it.
    share = _session_share("Parser refactor", options={"show_title": False})
    assert view_tracking._share_descriptor(share) == "session share 'Parser refactor'"


def test_descriptor_truncates_a_long_path():
    share = _artifact_share("", relative_path="deep/" + "x" * 200 + ".html")
    name = view_tracking._share_descriptor(share).removeprefix("artifact share ")
    assert name.endswith("…'") and len(name) == 83  # 80 chars + the ellipsis + the quotes


# ── _maybe_notify ───────────────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    """Capture ``_send`` calls and stub the settings read; reset the throttle state."""
    import twicc.external_notifications as ext
    import twicc.synced_settings as synced

    calls = []

    async def fake_send(urls, title, body):
        calls.append((urls, title, body))

    monkeypatch.setattr(ext, "_send", fake_send)
    monkeypatch.setattr(synced, "read_synced_settings", lambda: {
        "externalNotificationTargets": [{"enabled": True, "url": "json://x", "tested": True}],
    })
    monkeypatch.setattr(view_tracking, "_notify_state", {})
    return calls


def test_maybe_notify_uses_the_descriptor(sent):
    share = _session_share("Parser refactor", "client demo")
    share.notify_on_view = True
    asyncio.run(view_tracking._maybe_notify(share))
    assert sent == [(["json://x"], "Share viewed",
                     "Your session share 'Parser refactor' (client demo) was viewed.")]


def test_maybe_notify_skips_when_the_flag_is_off(sent):
    share = _session_share("Parser refactor")
    share.notify_on_view = False
    asyncio.run(view_tracking._maybe_notify(share))
    assert sent == []


def test_maybe_notify_throttles_and_reports_suppressed_views(sent):
    share = _session_share("Parser refactor")
    share.notify_on_view = True
    for _ in range(3):
        asyncio.run(view_tracking._maybe_notify(share))
    assert len(sent) == 1  # first view sends, the next two are within the hour
    # A later send (throttle window elapsed) reports what it suppressed.
    stale = time.monotonic() - view_tracking._NOTIFY_THROTTLE_SECONDS - 1
    view_tracking._notify_state[share.id] = (stale, 2)
    asyncio.run(view_tracking._maybe_notify(share))
    assert sent[-1][2] == (
        "Your session share 'Parser refactor' was viewed. (2 more views since the last alert)"
    )
