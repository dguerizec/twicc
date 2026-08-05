"""The artifact data-store helpers (design 2026-08-05 §3/§4): pure filesystem
byte-store confined to a document's ``data/`` subtree — target resolution,
atomic write with size caps, delete, recursive listing."""

from __future__ import annotations

import os

import pytest

from twicc.artifacts.data_store import (
    MAX_DATA_FILE_BYTES,
    MAX_DATA_TREE_BYTES,
    delete_data_file,
    list_data_dir,
    resolve_data_target,
    write_data_file,
)


@pytest.fixture
def doc_dir(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "index.html").write_bytes(b"<html></html>")
    return str(d)


# ── resolve_data_target ───────────────────────────────────────────────────────


def test_resolve_accepts_file_under_data(doc_dir):
    out = resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "x.json"))
    assert out == os.path.join(os.path.realpath(doc_dir), "data", "x.json")


def test_resolve_accepts_nested_file(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "a", "b.json")) is not None


def test_resolve_accepts_the_data_dir_itself(doc_dir):
    out = resolve_data_target(doc_dir, os.path.join(doc_dir, "data"))
    assert out == os.path.join(os.path.realpath(doc_dir), "data")


def test_resolve_rejects_outside_data(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "index.html")) is None
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "other", "x.json")) is None


def test_resolve_rejects_traversal(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "..", "index.html")) is None
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "..", "..", "x")) is None


def test_resolve_rejects_symlink_escape(doc_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    data = os.path.join(doc_dir, "data")
    os.makedirs(data)
    os.symlink(str(outside), os.path.join(data, "link"))
    assert resolve_data_target(doc_dir, os.path.join(data, "link", "x.json")) is None


# ── write_data_file ───────────────────────────────────────────────────────────


def _data_root(doc_dir):
    return os.path.join(os.path.realpath(doc_dir), "data")


def test_write_creates_file_and_parents(doc_dir):
    root = _data_root(doc_dir)
    payload, status = write_data_file(root, os.path.join(root, "a", "b.json"), b'{"x":1}')
    assert status == 200
    assert payload["ok"] is True
    assert payload["size"] == 7
    with open(os.path.join(root, "a", "b.json"), "rb") as fp:
        assert fp.read() == b'{"x":1}'


def test_write_overwrites(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"one")
    payload, status = write_data_file(root, os.path.join(root, "x.json"), b"two!")
    assert status == 200
    with open(os.path.join(root, "x.json"), "rb") as fp:
        assert fp.read() == b"two!"


def test_write_refuses_file_over_cap(doc_dir):
    root = _data_root(doc_dir)
    payload, status = write_data_file(root, os.path.join(root, "big"), b"x" * (MAX_DATA_FILE_BYTES + 1))
    assert status == 413
    assert payload["error"] == "too_large"
    assert payload["max_bytes"] == MAX_DATA_FILE_BYTES
    assert not os.path.exists(os.path.join(root, "big"))


def test_write_refuses_tree_over_quota(doc_dir, monkeypatch):
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_TREE_BYTES", 10)
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "a"), b"12345678")
    payload, status = write_data_file(root, os.path.join(root, "b"), b"123")
    assert status == 413
    assert payload["error"] == "quota_exceeded"


def test_write_quota_counts_replaced_file_once(doc_dir, monkeypatch):
    # Overwriting an 8-byte file with 9 bytes under a 10-byte quota must pass:
    # the old size is reclaimed by the replace.
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_TREE_BYTES", 10)
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "a"), b"12345678")
    payload, status = write_data_file(root, os.path.join(root, "a"), b"123456789")
    assert status == 200


# ── delete_data_file ──────────────────────────────────────────────────────────


def test_delete_removes_file(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"{}")
    payload, status = delete_data_file(os.path.join(root, "x.json"))
    assert status == 200 and payload["ok"] is True
    assert not os.path.exists(os.path.join(root, "x.json"))


def test_delete_missing_is_404(doc_dir):
    payload, status = delete_data_file(os.path.join(_data_root(doc_dir), "nope"))
    assert status == 404


def test_delete_directory_is_400(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "sub", "x"), b"1")
    payload, status = delete_data_file(os.path.join(root, "sub"))
    assert status == 400


# ── list_data_dir ─────────────────────────────────────────────────────────────


def test_list_recursive_relative_paths(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"{}")
    write_data_file(root, os.path.join(root, "sub", "y.bin"), b"12345")
    payload, status = list_data_dir(root)
    assert status == 200
    by_path = {f["path"]: f for f in payload["files"]}
    assert set(by_path) == {"x.json", "sub/y.bin"}
    assert by_path["sub/y.bin"]["size"] == 5
    assert isinstance(by_path["x.json"]["mtime"], str)


def test_list_missing_data_dir_is_empty(doc_dir):
    payload, status = list_data_dir(_data_root(doc_dir))
    assert status == 200
    assert payload == {"files": []}
