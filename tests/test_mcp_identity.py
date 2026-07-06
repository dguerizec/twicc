"""Session-token mint/verify and the Codex draft-id alias map."""

import pytest

from twicc.mcp import identity


@pytest.fixture(autouse=True)
def _isolated_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "twicc.paths.get_mcp_secret_path", lambda: tmp_path / "mcp-secret",
    )
    identity._reset_for_tests()
    yield
    identity._reset_for_tests()


def test_mint_and_resolve_roundtrip():
    token = identity.mint_session_token("abc-123")
    assert token.startswith("twicc_mcp_")
    assert identity.resolve_session_token(token) == "abc-123"


def test_token_is_deterministic_across_secret_reloads():
    t1 = identity.mint_session_token("abc-123")
    identity._reset_for_tests()  # drop the cached secret; file persists
    assert identity.mint_session_token("abc-123") == t1


def test_tampered_token_rejected():
    token = identity.mint_session_token("abc-123")
    sid, _, sig = token.removeprefix("twicc_mcp_").rpartition(".")
    forged = f"twicc_mcp_other-session.{sig}"
    assert identity.resolve_session_token(forged) is None
    assert identity.resolve_session_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert identity.resolve_session_token("garbage") is None
    assert identity.resolve_session_token("") is None


def test_secret_file_created_with_0600(tmp_path):
    identity.mint_session_token("abc")
    path = tmp_path / "mcp-secret"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_draft_alias_resolution():
    token = identity.mint_session_token("draft-id")
    identity.register_draft_alias("draft-id", "canonical-id")
    assert identity.resolve_session_token(token) == "canonical-id"
