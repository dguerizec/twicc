"""Resolution and creation of per-session agent work directories."""

from __future__ import annotations

import asyncio

import pytest

from twicc import paths
from twicc.agent.work_dirs import resolve_and_create_work_dirs
from twicc.pending_session_attributes import (
    pop_pending_session_attributes,
    set_pending_session_attributes,
)


@pytest.mark.django_db(transaction=True)
def test_codex_canonical_dirs_use_draft_pending_spawn_root(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    set_pending_session_attributes("draft-id", spawn_root_id="root-session")

    try:
        result = asyncio.run(
            resolve_and_create_work_dirs(
                "canonical-id",
                pending_id="draft-id",
            )
        )
    finally:
        pop_pending_session_attributes("draft-id")

    expected = [
        data_dir / "artifacts" / "canonical-id",
        data_dir / "scratch" / "canonical-id",
        data_dir / "scratch" / "root-session",
    ]
    assert result == [str(path) for path in expected]
    assert all(path.is_dir() for path in expected)
