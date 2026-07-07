"""call_tool dispatch: identity binding, in-process execution, envelope."""

import asyncio

import pytest

from twicc import paths
from twicc.mcp import server as mcp_server


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)

    async def _noop():
        return None

    monkeypatch.setattr("twicc.workspaces._broadcast_after_write", _noop)
    return data_dir


@pytest.mark.django_db(transaction=True)
def test_call_tool_runs_command_and_returns_envelope():
    result = asyncio.run(mcp_server.dispatch_tool("workspaces", {}, session_id=None))
    assert set(result) == {"exit_code", "result", "error"}
    assert result["exit_code"] == 0


@pytest.mark.django_db(transaction=True)
def test_call_tool_whoami_uses_bound_identity(isolated_data_dir):
    import os

    import orjson

    from twicc.core.models import Project, Session

    # whoami refuses to run without a live backend sidecar: write one pointing
    # at this (alive) test process.
    (isolated_data_dir / "twicc.info.json").write_bytes(
        orjson.dumps({"pid": os.getpid(), "port": 3500, "started_at": "2026-07-06T00:00:00Z"}),
    )
    project = Project.objects.create(id="-tmp-p2", directory="/tmp/p2", name="p2")
    session = Session.objects.create(
        id="22222222-2222-2222-2222-222222222222", project=project,
        provider="claude_code", file_path="p2.jsonl",
    )
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id=session.id))
    assert result["exit_code"] == 0
    assert result["result"]["session"]["id"] == session.id


@pytest.mark.django_db(transaction=True)
def test_call_tool_mutation_bypasses_drop_files(tmp_path, monkeypatch, isolated_data_dir):
    drops = tmp_path / "drops"
    drops.mkdir()
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: drops,
    )
    result = asyncio.run(mcp_server.dispatch_tool(
        "create_workspace", {"name": "ws-via-mcp"}, session_id=None,
    ))
    assert result["exit_code"] == 0
    assert result["result"]["status"] == "created"
    assert list(drops.iterdir()) == []


def test_unknown_tool_raises():
    with pytest.raises(mcp_server.UnknownToolError):
        asyncio.run(mcp_server.dispatch_tool("nope", {}, session_id=None))


def test_call_tool_normalizes_datetime_to_json_native(monkeypatch):
    """Command results carry orjson-native objects (datetime timestamps, e.g.
    ``session … messages``). dispatch_tool must hand the MCP SDK plain JSON
    types — the SDK serializes the envelope with stdlib ``json.dumps``, which
    chokes on datetime ("Object of type datetime is not JSON serializable").
    """
    import json
    from datetime import datetime, timezone

    from twicc.rpc.invoker import InvocationResult

    ts = datetime(2026, 7, 6, 19, 14, 24, 421000, tzinfo=timezone.utc)
    monkeypatch.setattr(
        mcp_server,
        "_run_invoke",
        lambda argv: InvocationResult(exit_code=0, result=[{"timestamp": ts}], error=None),
    )
    result = asyncio.run(mcp_server.dispatch_tool("workspaces", {}, session_id=None))
    # The SDK does exactly this on the returned dict; it must not raise.
    json.dumps(result)
    assert result["result"][0]["timestamp"] == "2026-07-06T19:14:24.421000+00:00"
