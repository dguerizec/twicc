"""Regression for the Phase-2 review's Critical C1: the SHARE broker proxy must
never honour a viewer-supplied ``pinned_ip`` (SSRF pivot into loopback/LAN)."""

import asyncio

import orjson
from django.test import RequestFactory

from twicc.artifacts import proxy as proxy_module
from twicc.artifacts.proxy import ProxyResult, ResolvedTarget


def _run(coro):
    return asyncio.run(coro)


def _req(body: dict):
    return RequestFactory().post(
        "/share/tkn/api/proxy/", data=orjson.dumps(body), content_type="application/json"
    )


def test_share_proxy_ignores_client_pinned_ip(monkeypatch):
    """With an allowlist enforced, a viewer POSTing ``pinned_ip: 127.0.0.1`` must not
    steer the connection there — the server resolves the allowlisted host itself."""
    seen = {}

    async def fake_resolve(host, port, **kw):
        # The real DNS for the allowlisted host → a public IP.
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    async def fake_fetch(*, method, url, headers, body, pinned_ip, client, **kw):
        seen["pinned_ip"] = pinned_ip
        return ProxyResult(status=200, reason="OK", headers={}, body=b"ok")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    monkeypatch.setattr(proxy_module, "proxy_fetch", fake_fetch)

    req = _req({"mode": "fetch", "pinned_ip": "127.0.0.1",
                "request": {"url": "https://api.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(req, enforced_allowlist={"https://api.example.com:443"}))
    assert res.status_code == 200
    # The connection pinned the SERVER-resolved IP, never the client's loopback.
    assert seen["pinned_ip"] == "8.8.8.8"


def test_share_proxy_blocks_non_allowlisted_host(monkeypatch):
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    req = _req({"mode": "fetch", "request": {"url": "https://evil.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(req, enforced_allowlist={"https://api.example.com:443"}))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "not_allowed"}


def test_share_proxy_refuses_preflight():
    req = _req({"mode": "preflight", "request": {"url": "https://api.example.com/"}})
    res = _run(proxy_module.artifact_proxy(req, enforced_allowlist={"https://api.example.com:443"}))
    assert res.status_code == 400
    assert orjson.loads(res.content)["reason"] == "preflight_disabled"


def test_share_proxy_still_blocks_metadata_even_if_allowlisted(monkeypatch):
    """Metadata hard-block fires before the allowlist check (defence in depth)."""
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="169.254.169.254", kind="metadata")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    req = _req({"mode": "fetch", "request": {"url": "https://api.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(req, enforced_allowlist={"https://api.example.com:443"}))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "metadata"}
