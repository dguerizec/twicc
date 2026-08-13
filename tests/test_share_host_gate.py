"""The mandatory dedicated share origin gate (design §12): /share/ is served ONLY
on the shareBaseUrl host, never on the working origin."""

import asyncio

import pytest

from twicc.share.asgi_filter import ShareHostGate, ShareOnlyApp


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Recorder:
    """A stub full-app that answers 200 and records that it was reached."""
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"full-app"})


async def _drive(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(m):
        sent.append(m)

    await app(scope, receive, send)
    return sent


def _http(path, host):
    return {"type": "http", "path": path, "headers": [(b"host", host.encode())]}


def _ws(path, host):
    return {"type": "websocket", "path": path, "headers": [(b"host", host.encode())]}


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def _ws_close_code(sent):
    for m in sent:
        if m["type"] == "websocket.close":
            return m.get("code")
    return None


def _location(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            for name, value in m["headers"]:
                if name == b"location":
                    return value.decode("latin1")
    return None


@pytest.fixture
def set_share_host(monkeypatch):
    def _set(value):
        monkeypatch.setattr("twicc.synced_settings.read_synced_settings",
                            lambda: {"shareBaseUrl": value})
    return _set


def _gate():
    full = Recorder()
    return ShareHostGate(full, ShareOnlyApp(full)), full


# ── Share host unset → sharing disabled everywhere ──────────────────────────

def test_unset_host_404s_share_everywhere(set_share_host):
    set_share_host("")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    assert _status(sent) == 404
    assert not full.called


def test_unset_host_serves_working_app(set_share_host):
    set_share_host("")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_invalid_host_disables_sharing(set_share_host):
    set_share_host("ftp://share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 404
    assert not full.called


# ── On the share host → only the share surface ──────────────────────────────

def test_share_host_serves_share(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_share_host_404s_non_share_paths(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "share.example.com")))
    assert _status(sent) == 404
    assert not full.called


def test_share_host_allows_shared_assets(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/_twicc/artifact-shell/shell.js", "share.example.com")))
    assert _status(sent) == 200


def test_share_host_favicon_204(set_share_host):
    set_share_host("share.example.com")
    gate, _full = _gate()
    sent = _run(_drive(gate, _http("/favicon.ico", "share.example.com")))
    assert _status(sent) == 204


def test_share_host_root_redirects_to_share_temporarily(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/", "share.example.com")))
    # 302 (temporary), NOT 301/308 — a real homepage could live here later.
    assert _status(sent) == 302
    assert _status(sent) not in (301, 308)
    assert _location(sent) == "/share/"
    assert not full.called


def test_working_origin_root_serves_app(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    # On the working origin, / is the SPA — never redirected to /share/.
    sent = _run(_drive(gate, _http("/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── On the working origin → share surface invisible ─────────────────────────

def test_working_origin_404s_share(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    assert _status(sent) == 404
    assert not full.called


def test_working_origin_serves_app(set_share_host):
    set_share_host("share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_full_url_share_base_extracts_hostname(set_share_host):
    set_share_host("https://share.example.com/")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── WebSocket ───────────────────────────────────────────────────────────────

def test_ws_share_closed_on_working_origin(set_share_host):
    set_share_host("share.example.com")
    gate, _full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "app.example.com")))
    assert _ws_close_code(sent) == 4404


def test_ws_non_share_closed_on_share_host(set_share_host):
    set_share_host("share.example.com")
    gate, _full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "share.example.com")))
    assert _ws_close_code(sent) == 4404
