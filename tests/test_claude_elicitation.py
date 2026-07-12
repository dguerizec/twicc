"""Unit tests for the Claude Code MCP elicitation support.

Two pure layers are covered:

- ``make_elicitation_pending_request`` (the CLI control-request params →
  provider-neutral ``PendingRequest`` with the shared-component tool_input
  key set);
- ``ClaudeCodeWSHandler._build_elicitation_response`` (the frontend payload →
  CLI wire response ``{action, content?}``; ``None`` on invalid input, which
  the caller replaces with the safe ``cancel`` default).

The bridge itself (``Query._handle_control_request`` widening) is exercised
end-to-end against the live CLI, not unit-tested here — its behaviour depends
on the SDK internals it wraps.
"""

from __future__ import annotations

import uuid

import pytest

from twicc.providers.claude_code.agent.elicitation import (
    ELICITATION_TOOL_NAMES,
    default_elicitation_response,
    make_elicitation_pending_request,
)
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler


@pytest.fixture
def handler():
    # ``_build_elicitation_response`` never touches ``self.consumer`` —
    # passing None keeps this a pure unit test.
    return ClaudeCodeWSHandler(consumer=None)


_FORM_PARAMS = {
    "subtype": "elicitation",
    "mcp_server_name": "myserver",
    "message": "Please fill in your profile",
    "mode": "form",
    "requested_schema": {
        "type": "object",
        "properties": {"nickname": {"type": "string"}},
        "required": ["nickname"],
    },
}

_URL_PARAMS = {
    "subtype": "elicitation",
    "mcp_server_name": "myserver",
    "message": "Authorize in your browser",
    "mode": "url",
    "url": "https://example.com/authorize",
    "elicitation_id": "elic-123",
}


class TestMakeElicitationPendingRequest:
    def test_form_mode(self):
        request = make_elicitation_pending_request(_FORM_PARAMS)
        assert request.tool_name == "elicitationForm"
        assert request.request_type == "ask_user_question"
        assert request.tool_input == {
            "serverName": "myserver",
            "message": "Please fill in your profile",
            "mode": "form",
            "requestedSchema": _FORM_PARAMS["requested_schema"],
        }
        assert request.permission_suggestions is None

    def test_url_mode(self):
        request = make_elicitation_pending_request(_URL_PARAMS)
        assert request.tool_name == "elicitationUrl"
        assert request.request_type == "ask_user_question"
        assert request.tool_input == {
            "serverName": "myserver",
            "message": "Authorize in your browser",
            "mode": "url",
            "url": "https://example.com/authorize",
            "elicitationId": "elic-123",
        }

    def test_missing_mode_defaults_to_form(self):
        params = dict(_FORM_PARAMS)
        del params["mode"]
        request = make_elicitation_pending_request(params)
        assert request.tool_name == "elicitationForm"
        assert request.tool_input["mode"] == "form"

    def test_unknown_mode_degrades_to_form(self):
        # Schema drift must degrade to a visible (if plain) form prompt,
        # never a silent drop.
        request = make_elicitation_pending_request({**_FORM_PARAMS, "mode": "hologram"})
        assert request.tool_name == "elicitationForm"
        assert request.tool_input["mode"] == "hologram"

    def test_none_and_junk_params(self):
        for params in (None, "junk", 42):
            request = make_elicitation_pending_request(params)
            assert request.tool_name == "elicitationForm"
            assert request.tool_input == {"serverName": "", "message": "", "mode": "form"}

    def test_request_id_is_uuid(self):
        request = make_elicitation_pending_request(_FORM_PARAMS)
        uuid.UUID(request.request_id)  # raises on a non-UUID

    def test_tool_names_are_the_shared_set(self):
        assert ELICITATION_TOOL_NAMES == {"elicitationForm", "elicitationUrl"}


class TestBuildElicitationResponse:
    @pytest.mark.parametrize("tool_name", sorted(ELICITATION_TOOL_NAMES))
    def test_accept_with_content(self, handler, tool_name):
        result = handler._build_elicitation_response({
            "tool_name": tool_name,
            "action": "accept",
            "content": {"nickname": "Twidi"},
        })
        assert result == {"action": "accept", "content": {"nickname": "Twidi"}}

    def test_accept_without_content(self, handler):
        # A schema-less confirm: accept with no form values.
        result = handler._build_elicitation_response({
            "tool_name": "elicitationUrl",
            "action": "accept",
        })
        assert result == {"action": "accept"}

    @pytest.mark.parametrize("action", ["decline", "cancel"])
    def test_non_accept_actions(self, handler, action):
        result = handler._build_elicitation_response({
            "tool_name": "elicitationForm",
            "action": action,
        })
        assert result == {"action": action}

    @pytest.mark.parametrize("action", ["decline", "cancel"])
    def test_content_with_non_accept_returns_none(self, handler, action):
        result = handler._build_elicitation_response({
            "tool_name": "elicitationForm",
            "action": action,
            "content": {"nickname": "Twidi"},
        })
        assert result is None

    @pytest.mark.parametrize("action", ["approve", "", None, 42, {"action": "accept"}])
    def test_invalid_action_returns_none(self, handler, action):
        result = handler._build_elicitation_response({
            "tool_name": "elicitationForm",
            "action": action,
        })
        assert result is None

    def test_non_dict_content_returns_none(self, handler):
        result = handler._build_elicitation_response({
            "tool_name": "elicitationForm",
            "action": "accept",
            "content": ["not", "a", "dict"],
        })
        assert result is None

    def test_default_is_cancel(self):
        assert default_elicitation_response() == {"action": "cancel"}
        # Fresh dict on every call — callers may mutate freely.
        assert default_elicitation_response() is not default_elicitation_response()
