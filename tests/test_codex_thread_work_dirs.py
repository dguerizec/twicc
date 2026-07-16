"""Codex thread-level work-directory configuration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from openai_codex.generated.v2_all import ApprovalsReviewer

from twicc.providers.codex.agent import manager as manager_module
from twicc.providers.codex.agent.manager import CodexAgentManager, _apply_codex_work_dirs
from twicc.providers.helpers import AgentSettings


class _FakeCodex:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []

    async def thread_start_with_policy(self, **kwargs):
        self.start_calls.append(deepcopy(kwargs))
        return SimpleNamespace(id="canonical-id")

    async def thread_resume_with_policy(self, thread_id, **kwargs):
        self.resume_calls.append((thread_id, deepcopy(kwargs)))
        return SimpleNamespace(id=thread_id)

    async def close(self) -> None:
        pass


class _FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.seeded_pending_id: str | None = None
        self.context_reset = False

    async def _seed_context_baseline(self, *, pending_id: str) -> None:
        self.seeded_pending_id = pending_id

    def _reset_context_baseline(self) -> None:
        self.context_reset = True


def _install_factory_fakes(monkeypatch, work_dirs: list[str]):
    codex = _FakeCodex()
    resolved: list[tuple[str, str | None]] = []

    async def fake_make_codex_config(*, cwd):
        return {"cwd": cwd}

    async def fake_resolve(session_id, *, pending_id=None):
        resolved.append((session_id, pending_id))
        return work_dirs

    monkeypatch.setattr(manager_module, "make_codex_config", fake_make_codex_config)
    monkeypatch.setattr(manager_module, "TwiccAsyncCodex", lambda *, config: codex)
    monkeypatch.setattr(manager_module, "attach_stderr_logging", lambda *args: None)
    monkeypatch.setattr(manager_module, "resolve_and_create_work_dirs", fake_resolve)
    monkeypatch.setattr(manager_module, "CodexAgent", _FakeAgent)
    monkeypatch.setattr(manager_module, "inject_context", lambda *args, **kwargs: None)

    from twicc import mcp
    from twicc.core.services import trust
    from twicc.mcp import identity

    monkeypatch.setattr(mcp, "mcp_enabled", lambda: False)
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project_id: False)
    monkeypatch.setattr(identity, "register_draft_alias", lambda *args: None)
    return codex, resolved


def test_new_thread_is_resumed_with_canonical_work_dirs(monkeypatch) -> None:
    roots = ["/data/artifacts/canonical-id", "/data/scratch/canonical-id"]
    codex, resolved = _install_factory_fakes(monkeypatch, roots)

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "draft-id",
            "project-id",
            "/project",
            resume=False,
            settings=AgentSettings(permission_mode="auto_review"),
        )

    agent = asyncio.run(scenario())

    assert resolved == [("canonical-id", "draft-id")]
    assert len(codex.start_calls) == 1
    assert "sandbox_workspace_write" not in codex.start_calls[0]["config"]
    assert len(codex.resume_calls) == 1
    thread_id, resume = codex.resume_calls[0]
    assert thread_id == "canonical-id"
    assert resume["config"]["sandbox_workspace_write"]["writable_roots"] == roots
    assert resume["approvals_reviewer"] is ApprovalsReviewer.auto_review
    assert agent.kwargs["work_dirs"] == roots
    assert agent.seeded_pending_id == "draft-id"


def test_existing_thread_gets_work_dirs_in_first_resume(monkeypatch) -> None:
    roots = ["/data/artifacts/thread-id", "/data/scratch/thread-id"]
    codex, resolved = _install_factory_fakes(monkeypatch, roots)

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "thread-id",
            "project-id",
            "/project",
            resume=True,
            settings=AgentSettings(permission_mode="auto_review"),
        )

    agent = asyncio.run(scenario())

    assert resolved == [("thread-id", None)]
    assert codex.start_calls == []
    assert len(codex.resume_calls) == 1
    _, resume = codex.resume_calls[0]
    assert resume["config"]["sandbox_workspace_write"]["writable_roots"] == roots
    assert agent.kwargs["work_dirs"] == roots
    assert agent.context_reset is True


def test_work_dir_config_preserves_existing_workspace_settings() -> None:
    config = {"sandbox_workspace_write": {"network_access": True}}
    _apply_codex_work_dirs(config, ["/scratch/session"])

    assert config["sandbox_workspace_write"] == {
        "network_access": True,
        "writable_roots": ["/scratch/session"],
    }
