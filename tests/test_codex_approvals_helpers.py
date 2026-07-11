"""Unit tests for the Codex sync ↔ async approval bridge helpers.

The 3 server-side approval methods Codex emits
(``item/commandExecution/requestApproval``,
``item/fileChange/requestApproval``,
``item/permissions/requestApproval``) flow through this module on
the way to ``BaseAgent._await_pending_request``. The 4 helpers
covered here are pure, synchronous, and easy to lock down.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from twicc.providers.codex.agent.approvals import (
    APPROVAL_METHODS,
    ELICITATION_METHOD,
    REQUEST_USER_INPUT_METHOD,
    auto_approve_response_for,
    default_response_for,
    derive_request_id,
    is_approval_method,
    make_pending_request,
    resolve_tool_name,
)


# Single source of truth for the 3 legacy approval methods (mirrors
# APPROVAL_METHODS keys minus the 2 new ones; duplicated here so a future
# rename in the production module fails the table-driven assertions
# immediately). These 3 all have a static tool_name and are always
# ``tool_approval`` — the parametrized tests below rely on that.
KNOWN_METHODS = [
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
]

# The 2 new methods (MCP elicitation + requestUserInput), kept separate from
# KNOWN_METHODS because their tool_name is params-dependent and their
# request_type is "ask_user_question", not "tool_approval".
NEW_METHODS = [ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD]


class TestIsApprovalMethod:
    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_true_for_known_methods(self, method):
        assert is_approval_method(method) is True

    def test_false_for_unknown_method(self):
        assert is_approval_method("item/tool/call") is False

    def test_false_for_empty_string(self):
        assert is_approval_method("") is False

    def test_false_for_arbitrary_string(self):
        assert is_approval_method("nope") is False


class TestApprovalMethodsConstant:
    def test_constant_keys_match_known_methods(self):
        # APPROVAL_METHODS is a dict[wire_method, tool_name]. The keys
        # are the wire methods; the values are the human-readable
        # tool_names exposed in PendingRequest.tool_name (static for the
        # 3 legacy methods; the elicitation entry is refined per-request
        # by resolve_tool_name()).
        assert set(APPROVAL_METHODS.keys()) == set(KNOWN_METHODS) | set(NEW_METHODS)

    def test_tool_name_values_match_spec(self):
        # Source of truth for the (method → tool_name) mapping per spec
        # §1.1.{a,b,c}.
        assert APPROVAL_METHODS["item/commandExecution/requestApproval"] == "commandExecution"
        assert APPROVAL_METHODS["item/fileChange/requestApproval"] == "fileChange"
        assert APPROVAL_METHODS["item/permissions/requestApproval"] == "permissions"


class TestDeriveRequestId:
    def test_approval_id_takes_priority(self):
        params = {"approvalId": "appr-1", "itemId": "item-1"}
        assert derive_request_id(params) == "appr-1"

    def test_item_id_when_no_approval_id(self):
        params = {"itemId": "call_xyz"}
        assert derive_request_id(params) == "call_xyz"

    def test_uuid4_fallback_when_no_id_fields(self):
        result = derive_request_id({})
        # Should be a valid UUID4 string.
        parsed = UUID(result)
        assert parsed.version == 4

    def test_uuid4_fallback_when_none_params(self):
        result = derive_request_id(None)
        parsed = UUID(result)
        assert parsed.version == 4

    def test_empty_string_fields_fall_through_to_uuid(self):
        params = {"approvalId": "", "itemId": ""}
        result = derive_request_id(params)
        parsed = UUID(result)
        assert parsed.version == 4

    def test_non_string_id_fields_fall_through_to_uuid(self):
        # If the wire ever sends an int (defensive), we don't crash.
        params = {"approvalId": 123, "itemId": 456}
        result = derive_request_id(params)
        parsed = UUID(result)
        assert parsed.version == 4


class TestMakePendingRequest:
    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_returns_pending_request_for_known_methods(self, method):
        params = {"itemId": "call_abc"}
        pr = make_pending_request(method, params)
        # PendingRequest is a NamedTuple from twicc.agent.states.
        assert pr.request_id == "call_abc"
        # tool_name is looked up via APPROVAL_METHODS (the production
        # source of truth) — assert through that map rather than
        # re-deriving from the method string.
        assert pr.tool_name == APPROVAL_METHODS[method]
        # tool_input is a dict copy of the original params.
        assert isinstance(pr.tool_input, dict)
        assert pr.tool_input == params
        # request_type is always "tool_approval" for Codex (no ask_user_question).
        assert pr.request_type == "tool_approval"
        # permission_suggestions is unused by Codex.
        assert pr.permission_suggestions is None

    def test_empty_params_yield_empty_dict_tool_input(self):
        pr = make_pending_request(KNOWN_METHODS[0], None)
        assert pr.tool_input == {}

    def test_unknown_method_raises_value_error(self):
        with pytest.raises(ValueError):
            make_pending_request("item/unknown/requestApproval", {"itemId": "x"})

    def test_uuid_fallback_request_id_when_no_id_fields(self):
        pr = make_pending_request(KNOWN_METHODS[0], {})
        parsed = UUID(pr.request_id)
        assert parsed.version == 4


class TestDefaultResponseFor:
    def test_command_method_returns_decline(self):
        result = default_response_for("item/commandExecution/requestApproval")
        assert result == {"decision": "decline"}

    def test_file_method_returns_decline(self):
        result = default_response_for("item/fileChange/requestApproval")
        assert result == {"decision": "decline"}

    def test_permissions_method_returns_empty_grant(self):
        result = default_response_for("item/permissions/requestApproval")
        assert result == {"permissions": {}, "scope": "turn"}

    def test_unknown_method_raises_value_error(self):
        with pytest.raises(ValueError):
            default_response_for("item/unknown/requestApproval")

    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_returns_fresh_dict_per_call(self, method):
        # Defensive copy: mutating one return value must not leak into
        # the next call (no module-level cached dict).
        first = default_response_for(method)
        sentinel = object()
        key = next(iter(first))
        first[key] = sentinel
        second = default_response_for(method)
        assert second[key] is not sentinel


# ---------------------------------------------------------------------------
# New methods: MCP elicitation + requestUserInput (PR: codex-mcp-approvals)
# ---------------------------------------------------------------------------

MCP_APPROVAL_PARAMS = {
    "threadId": "t1", "turnId": "u1", "serverName": "chrome-devtools",
    "mode": "form",
    "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"]},
    "message": 'Allow the chrome-devtools MCP server to run tool "navigate_page"?',
    "requestedSchema": {"type": "object", "properties": {}},
}
GENERIC_FORM_PARAMS = {
    "threadId": "t1", "turnId": None, "serverName": "some-server",
    "mode": "form", "_meta": None, "message": "Fill this in",
    "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
}
URL_PARAMS = {
    "threadId": "t1", "turnId": None, "serverName": "some-server",
    "mode": "url", "_meta": None, "message": "Visit this",
    "url": "https://example.com/auth", "elicitationId": "e1",
}
REQUEST_USER_INPUT_PARAMS = {
    "threadId": "t1", "turnId": "u1", "itemId": "call_abc",
    "questions": [{
        "id": "mcp_tool_call_approval_call_abc", "header": "Approve app tool call?",
        "question": "Allow?", "isOther": False, "isSecret": False,
        "options": [{"label": "Allow", "description": "Run the tool and continue."}],
    }],
}


class TestNewMethodRecognition:
    @pytest.mark.parametrize("method", [ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD])
    def test_is_approval_method(self, method):
        assert is_approval_method(method) is True

    @pytest.mark.parametrize("params, expected", [
        (MCP_APPROVAL_PARAMS, "mcpToolCall"),
        (GENERIC_FORM_PARAMS, "elicitationForm"),
        (URL_PARAMS, "elicitationUrl"),
        (None, "elicitationForm"),          # defensive: no params → generic form
        ({"mode": "form", "_meta": {"codex_approval_kind": "tool_suggestion"}},
         "elicitationForm"),                # other approval kinds are NOT tool approvals
    ])
    def test_resolve_tool_name_elicitation(self, params, expected):
        assert resolve_tool_name(ELICITATION_METHOD, params) == expected

    def test_resolve_tool_name_request_user_input(self):
        assert resolve_tool_name(REQUEST_USER_INPUT_METHOD, REQUEST_USER_INPUT_PARAMS) \
            == "toolRequestUserInput"

    def test_resolve_tool_name_static_methods_unchanged(self):
        assert resolve_tool_name(
            "item/commandExecution/requestApproval", {"anything": 1},
        ) == "commandExecution"


class TestNewMethodPendingRequests:
    def test_mcp_tool_call_is_tool_approval(self):
        req = make_pending_request(ELICITATION_METHOD, MCP_APPROVAL_PARAMS)
        assert req.tool_name == "mcpToolCall"
        assert req.request_type == "tool_approval"
        assert req.tool_input["serverName"] == "chrome-devtools"
        assert req.tool_input["_meta"]["persist"] == ["session", "always"]

    @pytest.mark.parametrize("params, tool_name", [
        (GENERIC_FORM_PARAMS, "elicitationForm"),
        (URL_PARAMS, "elicitationUrl"),
    ])
    def test_elicitations_are_questions(self, params, tool_name):
        req = make_pending_request(ELICITATION_METHOD, params)
        assert req.tool_name == tool_name
        assert req.request_type == "ask_user_question"

    def test_request_user_input_is_question(self):
        req = make_pending_request(REQUEST_USER_INPUT_METHOD, REQUEST_USER_INPUT_PARAMS)
        assert req.tool_name == "toolRequestUserInput"
        assert req.request_type == "ask_user_question"
        # itemId doubles as the routing key (derive_request_id picks it up).
        assert req.request_id == "call_abc"

    def test_elicitation_request_id_is_uuid(self):
        # Elicitation params carry no approvalId/itemId → uuid4 fallback.
        req = make_pending_request(ELICITATION_METHOD, MCP_APPROVAL_PARAMS)
        UUID(req.request_id)  # raises if not a valid UUID

    def test_existing_methods_stay_tool_approval(self):
        req = make_pending_request(
            "item/commandExecution/requestApproval", {"itemId": "i1"},
        )
        assert req.request_type == "tool_approval"


class TestNewMethodDefaults:
    def test_elicitation_default_is_cancel(self):
        assert default_response_for(ELICITATION_METHOD) == {"action": "cancel"}

    def test_request_user_input_default_is_empty_answers(self):
        assert default_response_for(REQUEST_USER_INPUT_METHOD) == {"answers": {}}

    def test_defaults_are_fresh_dicts(self):
        a = default_response_for(ELICITATION_METHOD)
        b = default_response_for(ELICITATION_METHOD)
        assert a is not b

    @pytest.mark.parametrize("method", [
        ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD,
        "item/permissions/requestApproval",
    ])
    def test_auto_approve_rejects_non_path_methods(self, method):
        with pytest.raises(ValueError):
            auto_approve_response_for(method)
