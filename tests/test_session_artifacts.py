"""Tests for session-scoped artifact serving.

Covers:
- The pure :func:`_classify_artifact_filename` validator: empty, dot
  segments, leading dot, slashes, backslashes, unsupported extensions,
  happy paths and case-insensitive extensions.
- The :func:`session_artifact` view through Django's ``AsyncClient``:
  happy path, missing file, bad extension, symlink escape, HTTP method
  restrictions, and malformed URLs (subdirectories must NOT serve the
  SPA HTML).

The artifact directory is redirected to a ``tmp_path`` via monkeypatching
``twicc.paths.get_data_dir`` so the tests never touch the real data
directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from django.test import AsyncClient

from twicc import paths
from twicc.views import _classify_artifact_filename


# ---------------------------------------------------------------------------
# _classify_artifact_filename — pure function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("shot.png", "image/png"),
        ("shot.PNG", "image/png"),
        ("shot.jpg", "image/jpeg"),
        ("shot.jpeg", "image/jpeg"),
        ("shot.webp", "image/webp"),
        ("shot.gif", "image/gif"),
        ("shot.svg", "image/svg+xml"),
        ("a-b_c.0.png", "image/png"),
    ],
)
def test_classify_artifact_filename_accepts_valid(filename, expected):
    assert _classify_artifact_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        ".hidden.png",         # leading dot rejected by leading-char regex
        "no-extension",        # no extension
        "shot.txt",            # unsupported extension
        "shot.exe",            # unsupported extension
        "shot",                # no extension at all
        "shot.png.exe",        # final extension wins → unsupported
        "shot .png",           # spaces not allowed
        "sub/shot.png",        # slash in name
        "sub\\shot.png",       # backslash in name
        "../shot.png",         # traversal
        "..png",               # starts with dot
        "/abs/shot.png",       # absolute-looking
        "shot.PnG  ",          # trailing whitespace
    ],
)
def test_classify_artifact_filename_rejects_invalid(filename):
    assert _classify_artifact_filename(filename) is None


# ---------------------------------------------------------------------------
# Integration tests for the session_artifact view
# ---------------------------------------------------------------------------


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a"  # PNG signature
    "0000000d49484452"  # IHDR length + type
    "00000001000000010806000000"  # IHDR data: 1x1 RGBA
    "1f15c4890000000a4944415478da63000100000005000156aa"  # IDAT
    "60820000000049454e44ae426082"  # IEND
)


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path for the lifetime of the test.

    All downstream helpers (:func:`get_artifacts_dir`,
    :func:`get_session_artifacts_dir`) read from :func:`get_data_dir`, so
    monkeypatching that single function reroutes the whole tree.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def client(settings):
    """Async client with password protection disabled for the test scope.

    The default ``DJANGO_SETTINGS_MODULE`` (``twicc.settings``) inherits
    ``TWICC_PASSWORD_HASH`` from the developer's ``.env``. Tests that hit
    the HTTP stack would otherwise be denied by
    :class:`twicc.auth.middleware.PasswordAuthMiddleware`.
    """
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def _write_artifact(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target_dir = artifacts_root / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    target.write_bytes(payload)
    return target


def _run(coro):
    """Drive ``coro`` to completion from a sync test."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _get(client: AsyncClient, url: str):
    return _run(client.get(url))


def _read_body(response) -> bytes:
    if hasattr(response, "streaming_content"):
        return b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in response.streaming_content
        )
    return response.content


def test_serves_artifact_happy_path(client, artifacts_root):
    session_id = "sess-happy"
    _write_artifact(artifacts_root, session_id, "shot.png", PNG_BYTES)

    url = f"/artifacts/{session_id}/shot.png"
    response = _get(client, url)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert _read_body(response) == PNG_BYTES


def test_returns_404_for_missing_file(client, artifacts_root):
    response = _get(client, "/artifacts/sess-empty/nope.png")
    assert response.status_code == 404


def test_returns_404_for_unsupported_extension(client, artifacts_root):
    session_id = "sess-bad-ext"
    _write_artifact(artifacts_root, session_id, "data.txt", b"hello")

    response = _get(client, f"/artifacts/{session_id}/data.txt")
    assert response.status_code == 404


def test_rejects_symlink_escaping_artifacts_dir(client, artifacts_root, tmp_path):
    session_id = "sess-symlink"
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)

    session_dir = artifacts_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "shot.png").symlink_to(outside)

    response = _get(client, f"/artifacts/{session_id}/shot.png")
    assert response.status_code == 404


def test_post_method_rejected(client, artifacts_root):
    session_id = "sess-method"
    _write_artifact(artifacts_root, session_id, "shot.png", PNG_BYTES)
    response = _run(client.post(f"/artifacts/{session_id}/shot.png"))
    assert response.status_code == 405


def test_subdirectory_url_does_not_serve_spa(client, artifacts_root):
    """A URL with a subdirectory inside the file segment must NOT fall through
    to the SPA catch-all — the catch-all regex excludes ``/artifacts/`` to
    keep artifact URLs strictly within the artifact view (or 404)."""
    response = _get(client, "/artifacts/foo/sub/dir/shot.png")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Auth enforcement — the artifact prefix must be listed as protected so the
# ``PasswordAuthMiddleware`` does NOT silently let it through with the SPA
# bypass.
# ---------------------------------------------------------------------------


def test_artifact_urls_match_protected_non_api_prefixes():
    from twicc.auth.middleware import PROTECTED_NON_API_PREFIXES

    matches = lambda path: any(path.startswith(p) for p in PROTECTED_NON_API_PREFIXES)

    assert matches("/artifacts/foo/shot.png")
    assert matches("/artifacts/")
    # Non-artifact non-API paths must NOT be considered protected here.
    assert not matches("/project/foo/session/bar/artifacts/shot.png")
    assert not matches("/projects/foo")
    assert not matches("/")
    assert not matches("/login")
