"""Session-token mint/verify and the Codex draft-id alias map."""

import pytest

from twicc.mcp import identity


@pytest.fixture(autouse=True)
def _isolated_aliases():
    identity._reset_for_tests()
    yield
    identity._reset_for_tests()


def test_mint_and_resolve_roundtrip():
    token = identity.mint_session_token("abc-123")
    assert token.startswith("twicc_mcp_")
    assert identity.resolve_session_token(token) == "abc-123"


def test_token_is_deterministic():
    assert identity.mint_session_token("abc-123") == identity.mint_session_token("abc-123")


def test_secret_key_rotation_invalidates_tokens(settings):
    token = identity.mint_session_token("abc-123")
    settings.SECRET_KEY = "rotated-key"
    assert identity.resolve_session_token(token) is None
    assert identity.resolve_session_token(identity.mint_session_token("abc-123")) == "abc-123"


def test_tampered_token_rejected():
    token = identity.mint_session_token("abc-123")
    sid, _, sig = token.removeprefix("twicc_mcp_").rpartition(".")
    forged = f"twicc_mcp_other-session.{sig}"
    assert identity.resolve_session_token(forged) is None
    assert identity.resolve_session_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert identity.resolve_session_token("garbage") is None
    assert identity.resolve_session_token("") is None


def test_draft_alias_resolution():
    token = identity.mint_session_token("draft-id")
    identity.register_draft_alias("draft-id", "canonical-id")
    assert identity.resolve_session_token(token) == "canonical-id"


@pytest.mark.django_db
def test_forced_session_id_overrides_pid_walk():
    from twicc.cli._drop_request.whoami import forced_session_id, resolve_current_session
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="-tmp-proj", directory="/tmp/proj", name="proj")
    session = Session.objects.create(
        id="11111111-1111-1111-1111-111111111111", project=project,
    )
    token = forced_session_id.set(session.id)
    try:
        resolved = resolve_current_session()
        assert resolved is not None and resolved.id == session.id
    finally:
        forced_session_id.reset(token)


@pytest.mark.django_db
def test_forced_unknown_session_id_resolves_none():
    from twicc.cli._drop_request.whoami import forced_session_id, resolve_current_session

    token = forced_session_id.set("no-such-session")
    try:
        assert resolve_current_session() is None
    finally:
        forced_session_id.reset(token)
