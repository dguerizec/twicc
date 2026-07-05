"""Tests for the /_twicc/browser-companion.js serving endpoint."""

import asyncio

import pytest
from django.test import AsyncClient


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def built_script(settings, tmp_path):
    """Point PACKAGE_DIR at a temp tree containing a built companion file."""
    script_dir = tmp_path / "static" / "browser-companion"
    script_dir.mkdir(parents=True)
    script = script_dir / "companion.js"
    script.write_text("// built companion\n")
    settings.PACKAGE_DIR = tmp_path
    return script


def test_serves_built_script(client, built_script):
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
    assert b"built companion" in b"".join(response.streaming_content)


def test_404_when_not_built(client, settings, tmp_path):
    settings.PACKAGE_DIR = tmp_path
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 404


def test_post_not_allowed(client, built_script):
    response = _run(client.post("/_twicc/browser-companion.js"))
    assert response.status_code == 405


def test_open_when_password_configured(settings, built_script):
    # The user's dev page fetches the script unauthenticated: the endpoint
    # must pass the auth middleware even when a password is set.
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$dummy"
    client = AsyncClient()
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 200
