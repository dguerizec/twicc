"""Cache-validation tests for the SPA index document."""

import asyncio

import pytest
from django.test import AsyncClient


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def index_file(settings, tmp_path):
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    index = frontend_dir / "index.html"
    index.write_text('<script src="/static/assets/index-first.js"></script>')
    settings.FRONTEND_DIST_DIR = frontend_dir
    return index


def test_spa_index_revalidates_an_unchanged_document(client, index_file):
    first = _run(client.get("/"))
    etag = first["ETag"]

    second = _run(client.get("/", headers={"If-None-Match": etag}))

    assert first.status_code == 200
    assert first["Cache-Control"] == "private, no-cache"
    assert second.status_code == 304
    assert second.content == b""
    assert second["ETag"] == etag
    assert second["Cache-Control"] == "private, no-cache"


def test_spa_index_sends_new_html_after_a_build(client, index_file):
    first = _run(client.get("/"))
    old_etag = first["ETag"]
    index_file.write_text('<script src="/static/assets/index-second.js"></script>')

    rebuilt = _run(client.get("/", headers={"If-None-Match": old_etag}))

    assert rebuilt.status_code == 200
    assert rebuilt["ETag"] != old_etag
    assert b"index-second.js" in rebuilt.content
