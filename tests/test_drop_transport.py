"""In-process execution of drop-request payloads (no files involved)."""

import asyncio

import pytest

from twicc import paths
from twicc.drop_requests_watcher import execute_drop_payload


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Reroute the data dir to a temp path and keep workspace mutations off the
    channel layer (None in tests). ``get_workspaces_path`` resolves through
    ``get_data_dir`` dynamically, so patching that one function reroutes the
    whole tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)

    async def _noop():
        return None

    monkeypatch.setattr("twicc.workspaces._broadcast_after_write", _noop)
    return data_dir


def test_execute_unknown_kind_returns_failed():
    status = asyncio.run(execute_drop_payload({"kind": "nope:nope"}, "nope:nope"))
    assert status["status"] == "failed"
    assert "Unknown payload kind" in status["error"]
    assert "failed_at" in status


@pytest.mark.django_db(transaction=True)
def test_execute_workspace_create_roundtrip(isolated_data_dir):
    payload = {"kind": "workspace:create", "name": "mcp-test-ws"}
    status = asyncio.run(execute_drop_payload(payload, "workspace:create"))
    assert status["status"] == "created", status
    assert status["workspace_id"]
    assert "created_at" in status
