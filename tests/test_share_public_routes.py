import asyncio

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone as djtz

from twicc.auth.hashers import hash_password
from twicc.core.models import Project, Session, SessionItem, SessionType, Share
from twicc.core.services.share_tokens import mint_token


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def session(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-shr-routes", directory="/tmp/shr-routes")
    sess = Session.objects.create(
        id="sess-routes", project=project, provider="claude_code",
        file_path="sess-routes.jsonl", type=SessionType.SESSION, title="Routes session",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=5,
    )
    # display_level: 1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY.
    for ln, dl in [(1, 1), (2, 2), (3, 2), (4, 3), (5, 1)]:
        SessionItem.objects.create(session=sess, line_num=ln, content="{}", display_level=dl, kind="user_message")
    return sess


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _share(session, **kw):
    return Share.objects.create(kind="session", token=mint_token(), session=session, **kw)


def test_meta_ok_and_headers(client, session):
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    res = _run(client.get(f"/share/{share.token}/api/meta/"))
    assert res.status_code == 200
    data = orjson.loads(res.content)
    assert data["kind"] == "session"
    assert data["session_id"] == "sess-routes"
    # Never leaks the private label / counters / token.
    assert "label" not in data
    assert "token" not in data
    assert "view_count" not in data
    # Share-hardening headers on every response.
    assert res["X-Robots-Tag"] == "noindex, nofollow"
    assert res["Referrer-Policy"] == "no-referrer"
    assert res["Cache-Control"] == "no-store"


def test_unknown_token_404(client, session):
    res = _run(client.get("/share/nope-nope-nope-unknown/api/meta/"))
    assert res.status_code == 404


def test_revoked_token_404(client, session):
    share = _share(session, options={"mode": "live"}, revoked_at=djtz.now())
    res = _run(client.get(f"/share/{share.token}/api/meta/"))
    assert res.status_code == 404  # uniform 404 — no revoked oracle


def test_items_requires_range(client, session):
    share = _share(session, options={"mode": "live"})
    res = _run(client.get(f"/share/{share.token}/api/items/"))
    assert res.status_code == 200
    assert "error" in orjson.loads(res.content)


def test_items_range_filters_debug_level(client, session):
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    res = _run(client.get(f"/share/{share.token}/api/items/?range=1:5"))
    assert res.status_code == 200
    lines = [it["line_num"] for it in orjson.loads(res.content)]
    # display_level 3 (line 4) is excluded in normal mode; 1,2,3,5 remain.
    assert lines == [1, 2, 3, 5]


def test_debug_mode_exposes_level_3(client, session):
    share = _share(session, options={"mode": "live", "max_display_mode": "debug"})
    res = _run(client.get(f"/share/{share.token}/api/items/?range=1:5"))
    lines = [it["line_num"] for it in orjson.loads(res.content)]
    assert lines == [1, 2, 3, 4, 5]  # debug ceiling includes level 3


def test_snapshot_frozen_line_clamps(client, session):
    share = _share(session, options={"mode": "snapshot", "max_display_mode": "normal", "frozen_at_line": 3})
    res = _run(client.get(f"/share/{share.token}/api/items/?range=1:5"))
    lines = [it["line_num"] for it in orjson.loads(res.content)]
    assert lines == [1, 2, 3]  # line 4 (debug) filtered, line 5 beyond the freeze


def test_password_share_meta_401(client, session):
    share = _share(session, options={"mode": "live"}, password_hash=hash_password("secret"))
    res = _run(client.get(f"/share/{share.token}/api/meta/"))
    assert res.status_code == 401
    assert orjson.loads(res.content)["error"] == "share_password_required"


def test_page_route_renders_html(client, session):
    share = _share(session, options={"mode": "live"})
    res = _run(client.get(f"/share/{share.token}/"))
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/html")
    assert b"twicc-share-data" in res.content
    # Document title carries the shared item's (real) title.
    assert b"<title>TwiCC - Shared session - Routes session</title>" in res.content


def test_page_title_hidden_and_escaped(client, session):
    # show_title off → generic document title, nothing session-specific.
    share = _share(session, options={"mode": "live", "show_title": False})
    res = _run(client.get(f"/share/{share.token}/"))
    assert b"<title>TwiCC - Shared session</title>" in res.content
    assert b"Routes session" not in res.content.split(b"twicc-share-data")[0]
    # display_title override is HTML-escaped in the <title>.
    share2 = _share(session, options={"mode": "live", "show_title": True,
                                      "display_title": "a <b> & 'c'"})
    res2 = _run(client.get(f"/share/{share2.token}/"))
    assert b"<title>TwiCC - Shared session - a &lt;b&gt; &amp;" in res2.content


def test_recent_homepage_renders(client, transactional_db):
    res = _run(client.get("/share/"))
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/html")
    assert b"recent" in res.content


def test_auth_get_renders_form_for_password_share(client, session):
    share = _share(session, options={"mode": "live"}, password_hash=hash_password("secret"))
    res = _run(client.get(f"/share/{share.token}/auth"))
    assert res.status_code == 200
    assert b"password" in res.content.lower()


def _make_subagent(session, sub_id, line):
    from twicc.core.models import AgentLink
    now = djtz.now()
    sub = Session.objects.create(
        id=sub_id, project=session.project, provider="claude_code",
        file_path=f"{sub_id}.jsonl", type=SessionType.SESSION, title=sub_id,
        created_at=now, last_new_content_at=now, user_message_count=0,
        parent_session=session, last_line=1,
    )
    SessionItem.objects.create(session=sub, line_num=1, content="{}", display_level=1, kind="user_message")
    AgentLink.objects.create(session=session, tool_use_line_num=line, tool_use_id=f"tu-{sub_id}", agent_id=sub_id)
    return sub


def test_snapshot_hides_post_freeze_subagents(client, session):
    # Review finding I2: a frozen snapshot must not disclose subagents spawned after
    # the freeze — neither in the list nor by direct id.
    _make_subagent(session, "sub-pre", 2)   # spawned at line 2 (before freeze=3)
    _make_subagent(session, "sub-post", 4)  # spawned at line 4 (after freeze)
    share = _share(session, options={"mode": "snapshot", "max_display_mode": "normal",
                                     "include_subagents": True, "frozen_at_line": 3})
    res = _run(client.get(f"/share/{share.token}/api/subagents/"))
    assert [s["agent_id"] for s in orjson.loads(res.content)] == ["sub-pre"]
    # Direct access to the post-freeze subagent → 404 (defence in depth).
    assert _run(client.get(f"/share/{share.token}/api/subagent/sub-post/items/metadata/")).status_code == 404
    # Pre-freeze subagent is reachable.
    assert _run(client.get(f"/share/{share.token}/api/subagent/sub-pre/items/metadata/")).status_code == 200


def test_live_share_lists_all_subagents(client, session):
    _make_subagent(session, "sub-a", 2)
    _make_subagent(session, "sub-b", 4)
    share = _share(session, options={"mode": "live", "include_subagents": True})
    res = _run(client.get(f"/share/{share.token}/api/subagents/"))
    assert sorted(s["agent_id"] for s in orjson.loads(res.content)) == ["sub-a", "sub-b"]


def test_subagents_hidden_when_include_subagents_false(client, session):
    _make_subagent(session, "sub-x", 2)
    share = _share(session, options={"mode": "live", "include_subagents": False})
    assert orjson.loads(_run(client.get(f"/share/{share.token}/api/subagents/")).content) == []
    assert _run(client.get(f"/share/{share.token}/api/subagent/sub-x/items/metadata/")).status_code == 404


def _link(session, use_line, result_line, tool_id, **kw):
    from twicc.core.models import ToolResultLink
    return ToolResultLink.objects.create(
        session=session, tool_use_line_num=use_line, tool_result_line_num=result_line,
        tool_use_id=tool_id, tool_result_at=djtz.now(), **kw,
    )


def test_tool_states_aggregates_visible_tools(client, session):
    _link(session, 2, 3, "tu-ok")
    _link(session, 2, 5, "tu-ok")  # second result row for the same call
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    res = _run(client.get(f"/share/{share.token}/api/tool-states/"))
    assert res.status_code == 200
    tools = orjson.loads(res.content)["tools"]
    assert tools["tu-ok"]["result_count"] == 2
    assert tools["tu-ok"]["tool_result_line_nums"] == [3, 5]
    assert tools["tu-ok"]["completed_at"] is not None


def test_tool_states_excludes_over_ceiling_tool_use(client, session):
    # Line 4 is DEBUG_ONLY: under a normal ceiling its state (incl. error text)
    # must not leak through the endpoint.
    _link(session, 4, 5, "tu-hidden", error="secret failure detail")
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    tools = orjson.loads(_run(client.get(f"/share/{share.token}/api/tool-states/")).content)["tools"]
    assert "tu-hidden" not in tools
    # Debug ceiling exposes it.
    share_dbg = _share(session, options={"mode": "live", "max_display_mode": "debug"})
    tools = orjson.loads(_run(client.get(f"/share/{share_dbg.token}/api/tool-states/")).content)["tools"]
    assert tools["tu-hidden"]["error"] == "secret failure detail"


def test_tool_states_snapshot_clamps_to_frozen_line(client, session):
    # tool_use at line 2 with one pre-freeze and one post-freeze result: only the
    # pre-freeze row counts. A post-freeze tool_use disappears entirely.
    _link(session, 2, 3, "tu-mid")
    _link(session, 2, 5, "tu-mid")
    _link(session, 5, 5, "tu-post")
    share = _share(session, options={"mode": "snapshot", "max_display_mode": "normal", "frozen_at_line": 3})
    tools = orjson.loads(_run(client.get(f"/share/{share.token}/api/tool-states/")).content)["tools"]
    assert tools["tu-mid"]["result_count"] == 1
    assert tools["tu-mid"]["tool_result_line_nums"] == [3]
    assert "tu-post" not in tools


def test_subagent_tool_states(client, session):
    sub = _make_subagent(session, "sub-ts", 2)
    _link(sub, 1, 1, "tu-sub")
    share = _share(session, options={"mode": "live", "include_subagents": True})
    res = _run(client.get(f"/share/{share.token}/api/subagent/sub-ts/tool-states/"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["tools"]["tu-sub"]["result_count"] == 1
    # include_subagents off → 404.
    share_off = _share(session, options={"mode": "live", "include_subagents": False})
    assert _run(client.get(f"/share/{share_off.token}/api/subagent/sub-ts/tool-states/")).status_code == 404
