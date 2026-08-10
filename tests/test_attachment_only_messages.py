"""Attachment-only messages: a follow-up carrying no text at all.

Both providers accept a user message made only of image / document blocks, so
TwiCC lets the composer (and the CLI) send one to an EXISTING session. Creating
a session still demands text — that is where the title comes from — which
``test_create_session_still_requires_text`` pins down.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.core.services.send_message import send_message_to_session_from_payload
from twicc.core.services.session_creation import create_session_from_payload
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.helpers import AgentSettings

IMAGE_BLOCK = {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
}
DOCUMENT_BLOCK = {
    "type": "document",
    "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="},
}


# ----------------------------------------------------------------------
# Service-level validation
# ----------------------------------------------------------------------


def _error_codes(result) -> list[str]:
    return [e.code for e in (result.errors or [])]


@pytest.mark.django_db
def test_send_message_rejects_a_fully_empty_payload() -> None:
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "does-not-matter", "text": ""},
    ))
    assert result.success is False
    assert "empty_text" in _error_codes(result)


@pytest.mark.django_db
def test_send_message_accepts_empty_text_with_images() -> None:
    """No ``empty_text`` error: validation moves on to the session lookup."""
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "unknown-session", "text": "", "images": [IMAGE_BLOCK]},
    ))
    assert result.success is False
    assert _error_codes(result) == ["session_not_found"]


@pytest.mark.django_db
def test_send_message_accepts_empty_text_with_documents() -> None:
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "unknown-session", "text": "", "documents": [DOCUMENT_BLOCK]},
    ))
    assert result.success is False
    assert _error_codes(result) == ["session_not_found"]


@pytest.mark.django_db
def test_create_session_still_requires_text() -> None:
    """Attachments do NOT stand in for the prompt when creating a session."""
    result = asyncio.run(create_session_from_payload({
        "session_id": "new-session",
        "project_id": "some-project",
        "provider": "claude_code",
        "text": "",
        "images": [IMAGE_BLOCK],
    }))
    assert result.success is False
    assert [e.code for e in (result.errors or [])] == ["empty_text"]


# ----------------------------------------------------------------------
# Claude Code: SDK prompt building
# ----------------------------------------------------------------------


def _make_claude_agent(monkeypatch: pytest.MonkeyPatch) -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="opus", permission_mode="bypassPermissions"),
        AsyncMock(return_value=None),
        AsyncMock(),
        AsyncMock(),
    )
    # The context reconciliation needs a DB row and a live socket; neither is
    # what this test is about.
    monkeypatch.setattr(agent, "_reconcile_context", AsyncMock())
    monkeypatch.setattr(
        "twicc.providers.claude_code.agent.agent.apply_pending_context",
        lambda session_id, text: text,
    )
    monkeypatch.setattr(
        "twicc.providers.claude_code.agent.agent.apply_goal_instruction",
        lambda text: text,
    )
    return agent


def _claude_content(agent: ClaudeCodeAgent, text: str, **kwargs) -> list[dict]:
    async def _run() -> list[dict]:
        stream = await agent._build_query_prompt(text, kwargs.get("images"), kwargs.get("documents"))
        message = await anext(stream)
        return message["message"]["content"]

    return asyncio.run(_run())


def test_claude_prompt_omits_the_text_block_when_text_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_claude_agent(monkeypatch)
    content = _claude_content(agent, "", images=[IMAGE_BLOCK], documents=[DOCUMENT_BLOCK])
    assert content == [IMAGE_BLOCK, DOCUMENT_BLOCK]


def test_claude_prompt_keeps_the_text_block_when_text_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_claude_agent(monkeypatch)
    content = _claude_content(agent, "Look at this", images=[IMAGE_BLOCK])
    assert content == [IMAGE_BLOCK, {"type": "text", "text": "Look at this"}]


def test_claude_prompt_falls_back_to_an_empty_text_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to send at all: keep the (empty) text block rather than an
    empty content array, which the SDK would refuse."""
    agent = _make_claude_agent(monkeypatch)
    assert _claude_content(agent, "") == [{"type": "text", "text": ""}]


# ----------------------------------------------------------------------
# Codex: turn input building
# ----------------------------------------------------------------------


def _make_codex_agent(monkeypatch: pytest.MonkeyPatch) -> CodexAgent:
    monkeypatch.setattr(
        "twicc.providers.codex.agent.agent.get_provider_helpers",
        lambda provider: SimpleNamespace(resolve_sdk_model=lambda selected: "gpt-terra"),
    )
    agent = CodexAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="gpt-terra", effort="high", permission_mode="auto"),
        MagicMock(),
        AsyncMock(),
    )
    monkeypatch.setattr(agent, "_reconcile_context", AsyncMock())
    monkeypatch.setattr(
        "twicc.providers.codex.agent.agent.apply_pending_context",
        lambda session_id, text: text,
    )
    return agent


def _codex_items(agent: CodexAgent, text: str, images: list[dict] | None) -> list:
    return asyncio.run(agent._build_turn_input(text, images))


def test_codex_turn_input_omits_the_text_item_when_text_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _codex_items(_make_codex_agent(monkeypatch), "", [IMAGE_BLOCK])
    assert [type(item).__name__ for item in items] == ["ImageInput"]
    assert items[0].url == "data:image/png;base64,iVBORw0KGgo="


def test_codex_turn_input_keeps_the_text_item_when_text_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _codex_items(_make_codex_agent(monkeypatch), "Look at this", [IMAGE_BLOCK])
    assert [type(item).__name__ for item in items] == ["ImageInput", "TextInput"]
    assert items[1].text == "Look at this"


def test_codex_turn_input_falls_back_to_an_empty_text_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every image block was skipped (non-base64 source): send the empty text
    rather than an empty input list."""
    agent = _make_codex_agent(monkeypatch)
    items = _codex_items(agent, "", [{"type": "image", "source": {"type": "url"}}])
    assert [type(item).__name__ for item in items] == ["TextInput"]
    assert items[0].text == ""


# ----------------------------------------------------------------------
# Sender header on a text-less inter-session message
# ----------------------------------------------------------------------


def test_sender_header_is_the_whole_body_when_there_is_no_text() -> None:
    from twicc.cli._drop_request.sender_header import prefix_sender_header

    caller = SimpleNamespace(id="caller-id", spawned_by_id=None, title="Worker 1")
    header = prefix_sender_header(
        "", caller, recipient_id="recipient-id", recipient_spawned_by_id=None,
    )
    assert header == ':: message from another session `caller-id` ("**Worker 1**")'
