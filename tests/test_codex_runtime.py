"""Tests for provisioning and pruning the downloaded Codex CLI runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from twicc.providers.codex import runtime


@pytest.fixture
def runtime_cache(tmp_path, monkeypatch):
    cache = tmp_path / "codex-runtime"
    cache.mkdir()
    monkeypatch.setattr(runtime, "_cache_root", lambda: cache)
    monkeypatch.setattr(runtime, "_ready_in_process", False)
    # A worktree shell exports it; the default here must be "cleanup enabled".
    monkeypatch.delenv(runtime._NO_CLEANUP_ENV, raising=False)
    return cache


def _make_runtime(cache: Path, version: str, *, ready: bool = True) -> Path:
    store = cache / version
    binary = store / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"codex")
    if ready:
        (store / ".ready").write_text(f"{version}\ntest-platform\n", encoding="utf-8")
    return store


def test_ensure_ready_runtime_removes_only_older_version_directories(runtime_cache):
    older = _make_runtime(runtime_cache, "0.143.0")
    current = _make_runtime(runtime_cache, runtime.CODEX_VERSION)
    newer = _make_runtime(runtime_cache, "999.0.0")
    incomplete_older = _make_runtime(runtime_cache, "0.142.0", ready=False)
    unrelated = runtime_cache / "downloads"
    unrelated.mkdir()
    old_lock = runtime_cache / "0.143.0.lock"
    old_lock.touch()

    assert runtime.ensure_codex_runtime_sync() == current

    assert not older.exists()
    assert not incomplete_older.exists()
    assert current.exists()
    assert newer.exists()
    assert unrelated.exists()
    assert old_lock.exists()


def test_freshly_downloaded_runtime_also_removes_older_versions(
    runtime_cache, monkeypatch
):
    older = _make_runtime(runtime_cache, "0.143.0")

    def fake_download_and_extract():
        _make_runtime(runtime_cache, runtime.CODEX_VERSION)

    monkeypatch.setattr(runtime, "_download_and_extract", fake_download_and_extract)

    current = runtime.ensure_codex_runtime_sync()

    assert current.exists()
    assert not older.exists()


def test_cleanup_env_kill_switch_keeps_older_versions(runtime_cache, monkeypatch):
    monkeypatch.setenv(runtime._NO_CLEANUP_ENV, "1")
    older = _make_runtime(runtime_cache, "0.143.0")
    current = _make_runtime(runtime_cache, runtime.CODEX_VERSION)

    assert runtime.ensure_codex_runtime_sync() == current
    assert older.exists()


def test_runtime_pruned_by_another_checkout_is_downloaded_again(
    runtime_cache, monkeypatch
):
    current = _make_runtime(runtime_cache, runtime.CODEX_VERSION)
    assert runtime.ensure_codex_runtime_sync() == current

    # Another checkout, pinned to a newer version, prunes ours mid-process.
    runtime.shutil.rmtree(current)
    downloads = []

    def fake_download_and_extract():
        downloads.append(_make_runtime(runtime_cache, runtime.CODEX_VERSION))

    monkeypatch.setattr(runtime, "_download_and_extract", fake_download_and_extract)

    assert runtime.ensure_codex_runtime_sync() == current
    assert len(downloads) == 1
    assert current.exists()


def test_cleanup_failure_does_not_hide_a_ready_runtime(runtime_cache, monkeypatch):
    older = _make_runtime(runtime_cache, "0.143.0")
    current = _make_runtime(runtime_cache, runtime.CODEX_VERSION)
    original_rmtree = runtime.shutil.rmtree

    def failing_rmtree(path):
        if path == older:
            raise PermissionError("read-only cache")
        original_rmtree(path)

    monkeypatch.setattr(runtime.shutil, "rmtree", failing_rmtree)

    assert runtime.ensure_codex_runtime_sync() == current
    assert current.exists()
    assert older.exists()
