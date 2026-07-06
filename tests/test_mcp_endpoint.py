"""End-to-end JSON-RPC over the raw-ASGI /mcp endpoint (sync tests, asyncio.run)."""

import asyncio
import contextlib

import httpx
import pytest

from twicc.mcp import identity
from twicc.mcp.endpoint import handle_mcp, mcp_lifespan


@pytest.fixture(autouse=True)
def _fresh_session_manager(monkeypatch):
    """The streamable-HTTP session manager's ``.run()`` is single-shot per
    instance; drop the process-wide singleton so each test starts a fresh one."""
    monkeypatch.setattr("twicc.mcp.server._session_manager", None)
    yield
    monkeypatch.setattr("twicc.mcp.server._session_manager", None)


HEADERS_BASE = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _rpc(method: str, params: dict | None = None, id_: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id_ is not None:
        msg["id"] = id_
    return msg


INIT = _rpc("initialize", {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
})


@contextlib.asynccontextmanager
async def _client():
    async with mcp_lifespan():
        transport = httpx.ASGITransport(app=handle_mcp, client=("127.0.0.1", 9999))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def test_unauthenticated_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post("/mcp", json=INIT, headers=HEADERS_BASE)

    assert asyncio.run(scenario()).status_code == 401


def test_bad_token_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post(
                "/mcp", json=INIT,
                headers={**HEADERS_BASE, "authorization": "Bearer twicc_mcp_x.deadbeef"},
            )

    assert asyncio.run(scenario()).status_code == 401


@pytest.mark.django_db(transaction=True)
def test_initialize_list_call_roundtrip():
    headers = {
        **HEADERS_BASE,
        "authorization": f"Bearer {identity.mint_session_token('some-session')}",
    }

    async def scenario():
        async with _client() as client:
            r = await client.post("/mcp", json=INIT, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["result"]["serverInfo"]["name"] == "twicc"

            r = await client.post(
                "/mcp", json=_rpc("notifications/initialized", id_=None), headers=headers,
            )
            assert r.status_code in (200, 202)

            r = await client.post("/mcp", json=_rpc("tools/list", {}, 2), headers=headers)
            assert r.status_code == 200, r.text
            names = {t["name"] for t in r.json()["result"]["tools"]}
            assert "whoami" in names and "create_session" in names

            r = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "workspaces", "arguments": {}}, 3),
                headers=headers,
            )
            assert r.status_code == 200, r.text
            payload = r.json()["result"]
            assert payload["structuredContent"]["exit_code"] == 0

    asyncio.run(scenario())
