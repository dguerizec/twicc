"""Unit tests for the Codex WS response builders.

The 5 builders on ``CodexWSHandler`` translate the frontend's
payload (``{tool_name, decision, ...}`` or
``{tool_name, permissions, scope}``) into the SDK-compliant dicts
that ``_async_approval_handler`` returns to Codex via the bridge.

Contract: return ``dict`` on valid input, ``None`` on invalid input.
``None`` triggers the upstream ``_safe_default_for`` fallback so the
SDK never receives a malformed wire response.
"""

from __future__ import annotations

import pytest

from twicc.providers.codex.ws import CodexWSHandler


@pytest.fixture
def handler():
    # The builders don't touch ``self.consumer`` — passing None is safe
    # for pure unit testing of the response builders.
    return CodexWSHandler(consumer=None)


class TestBuildCommandResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions_return_decision_key(self, handler, decision):
        result = handler._build_command_response(decision)
        assert result == {"decision": decision}

    def test_accept_with_execpolicy_amendment_happy(self, handler):
        amendment = [{"rule": "git status"}]
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
        })
        assert result == {
            "decision": {
                "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
            },
        }

    def test_apply_network_policy_amendment_happy(self, handler):
        amendment = {"host": "example.com"}
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
        })
        assert result == {
            "decision": {
                "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
            },
        }

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_command_response("totally_made_up") is None

    def test_amendment_with_non_list_returns_none(self, handler):
        # Inner value is "not a list" — fails isinstance check.
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": "not a list"},
        })
        assert result is None

    def test_amendment_with_non_dict_returns_none(self, handler):
        # Inner value is "not a dict" — fails isinstance check.
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": "not a dict"},
        })
        assert result is None

    def test_inner_payload_not_a_dict_returns_none(self, handler):
        # The payload for a variant key must itself be a dict (not e.g. a list).
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": "not a dict at all",
        })
        assert result is None

    def test_dict_with_no_recognized_key_returns_none(self, handler):
        assert handler._build_command_response({"randomKey": {}}) is None

    def test_dict_with_multiple_keys_returns_none(self, handler):
        # Spec: dict variant must have EXACTLY one key.
        assert handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
            "applyNetworkPolicyAmendment": {"network_policy_amendment": {}},
        }) is None

    def test_non_string_non_dict_returns_none(self, handler):
        assert handler._build_command_response(42) is None
        assert handler._build_command_response(None) is None


class TestBuildFileResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions(self, handler, decision):
        result = handler._build_file_response(decision)
        assert result == {"decision": decision}

    def test_dict_input_returns_none(self, handler):
        # fileChange has no amendment variants — dict input is invalid.
        assert handler._build_file_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
        }) is None

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_file_response("nonsense") is None

    def test_non_string_returns_none(self, handler):
        assert handler._build_file_response(None) is None
        assert handler._build_file_response(42) is None


class TestBuildPermissionsResponse:
    def test_happy_turn_scope(self, handler):
        content = {"permissions": {"network": True}, "scope": "turn"}
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {"network": True}, "scope": "turn"}

    def test_happy_session_scope(self, handler):
        content = {"permissions": {"fileSystem": []}, "scope": "session"}
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {"fileSystem": []}, "scope": "session"}

    def test_missing_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({"scope": "turn"}) is None

    def test_missing_scope_returns_none(self, handler):
        assert handler._build_permissions_response({"permissions": {}}) is None

    def test_invalid_scope_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": {}, "scope": "forever",
        }) is None

    def test_non_dict_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": "not a dict", "scope": "turn",
        }) is None

    def test_strict_auto_review_true_included_in_result(self, handler):
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": True,
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": True}

    def test_strict_auto_review_false_included_in_result(self, handler):
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": False,
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": False}

    def test_strict_auto_review_non_bool_not_included(self, handler):
        # Non-bool strictAutoReview is silently ignored (not a validation error).
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": "yes",
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn"}
        assert "strictAutoReview" not in result

    def test_empty_permissions_dict_accepted(self, handler):
        result = handler._build_permissions_response({"permissions": {}, "scope": "turn"})
        assert result == {"permissions": {}, "scope": "turn"}


class TestBuildCodexResponse:
    def test_command_execution_dispatches_to_command_builder(self, handler):
        result = handler._build_codex_response("commandExecution", {"decision": "accept"})
        assert result == {"decision": "accept"}

    def test_file_change_dispatches_to_file_builder(self, handler):
        result = handler._build_codex_response("fileChange", {"decision": "decline"})
        assert result == {"decision": "decline"}

    def test_permissions_dispatches_to_permissions_builder(self, handler):
        content = {"permissions": {}, "scope": "turn"}
        result = handler._build_codex_response("permissions", content)
        assert result == {"permissions": {}, "scope": "turn"}

    def test_command_execution_invalid_decision_returns_none(self, handler):
        result = handler._build_codex_response("commandExecution", {"decision": "bad_decision"})
        assert result is None

    def test_file_change_invalid_decision_returns_none(self, handler):
        result = handler._build_codex_response("fileChange", {"decision": "bad_decision"})
        assert result is None

    def test_permissions_invalid_scope_returns_none(self, handler):
        result = handler._build_codex_response("permissions", {"permissions": {}, "scope": "bad"})
        assert result is None

    def test_unknown_tool_name_returns_none(self, handler):
        # _build_codex_response logs an error and returns None for unknown tool_names.
        # It does NOT fall through to _safe_default_for — the caller is responsible.
        result = handler._build_codex_response("totally_made_up", {})
        assert result is None


class TestSafeDefaultFor:
    def test_command_execution_returns_decline(self, handler):
        result = handler._safe_default_for("commandExecution")
        assert result == {"decision": "decline"}

    def test_file_change_returns_decline(self, handler):
        result = handler._safe_default_for("fileChange")
        assert result == {"decision": "decline"}

    def test_permissions_returns_empty_grant_with_turn_scope(self, handler):
        result = handler._safe_default_for("permissions")
        assert result == {"permissions": {}, "scope": "turn"}

    def test_unknown_tool_returns_decline(self, handler):
        # The else branch returns {"decision": "decline"} for any unknown tool_name.
        result = handler._safe_default_for("nonsense")
        assert result == {"decision": "decline"}

    def test_another_unknown_tool_returns_decline(self, handler):
        result = handler._safe_default_for("totallyMadeUp")
        assert result == {"decision": "decline"}
