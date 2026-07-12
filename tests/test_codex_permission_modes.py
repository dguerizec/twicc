"""Unit tests for the Codex permission_mode preset mapping.

5 wire-value presets (since PR3) translate to ``(SandboxMode, AskForApproval)``
couples for ``thread_start`` / ``thread_resume`` and to ``(SandboxPolicy, AskForApproval)``
for per-turn overrides via ``thread.turn``. Both surfaces are exercised here so
any future regression in either path is caught immediately.
"""

from __future__ import annotations

import pytest

from openai_codex.generated.v2_all import (
    AskForApproval,
    DangerFullAccessSandboxPolicy,
    GranularAskForApproval,
    ReadOnlySandboxPolicy,
    SandboxMode,
    SandboxPolicy,
    WorkspaceWriteSandboxPolicy,
)

from twicc.providers.codex.permission_modes import (
    DEFAULT_MODE,
    _PRESET_MAP,
    _to_sandbox_policy,
    resolve_codex_policy,
    resolve_codex_turn_overrides,
)


# (wire_mode, sandbox_mode, approval_policy_expectation)
# The expectation is the wire string for the plain variants, or "granular" for
# yolo (a GranularAskForApproval object — see _assert_approval_matches).
PRESET_TABLE: list[tuple[str, SandboxMode, str]] = [
    ("read_only", SandboxMode.read_only, "on-request"),
    ("strict", SandboxMode.read_only, "never"),
    ("auto", SandboxMode.workspace_write, "on-request"),
    ("autonomous", SandboxMode.workspace_write, "never"),
    ("yolo", SandboxMode.danger_full_access, "granular"),
]


def _assert_approval_matches(approval: AskForApproval, expected: str) -> None:
    """Assert an AskForApproval matches its PRESET_TABLE expectation.

    ``yolo`` uses a granular policy whose only prompt-enabled flag is
    ``mcp_elicitations`` (elicitations reach the user, everything else stays
    autonomous — see ``permission_modes._YOLO_APPROVAL``); the other presets
    are plain string variants.
    """
    assert isinstance(approval, AskForApproval)
    if expected == "granular":
        assert isinstance(approval.root, GranularAskForApproval)
        granular = approval.root.granular
        assert granular.mcp_elicitations is True
        assert granular.sandbox_approval is False
        assert granular.rules is False
        assert granular.request_permissions is False
        assert granular.skill_approval is False
    else:
        assert approval.root.value == expected


class TestPresetMap:
    def test_preset_map_has_exactly_5_entries(self):
        assert len(_PRESET_MAP) == 5

    def test_default_mode_is_auto(self):
        assert DEFAULT_MODE == "auto"

    def test_default_mode_is_present_in_preset_map(self):
        assert DEFAULT_MODE in _PRESET_MAP


class TestResolveCodexPolicy:
    @pytest.mark.parametrize("mode,sandbox,approval_str", PRESET_TABLE)
    def test_known_mode_returns_expected_couple(self, mode, sandbox, approval_str):
        result_sandbox, result_approval = resolve_codex_policy(mode)
        assert result_sandbox is sandbox
        _assert_approval_matches(result_approval, approval_str)

    def test_none_falls_back_to_default(self):
        result = resolve_codex_policy(None)
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected

    def test_unknown_mode_falls_back_to_default(self):
        result = resolve_codex_policy("totally_made_up_mode")
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected

    def test_empty_string_falls_back_to_default(self):
        # ``mode or DEFAULT_MODE`` makes empty string equivalent to None.
        result = resolve_codex_policy("")
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected


class TestToSandboxPolicy:
    def test_read_only_returns_read_only_policy(self):
        result = _to_sandbox_policy(SandboxMode.read_only)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, ReadOnlySandboxPolicy)
        assert result.root.type == "readOnly"

    def test_workspace_write_returns_workspace_write_policy(self):
        result = _to_sandbox_policy(SandboxMode.workspace_write)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, WorkspaceWriteSandboxPolicy)
        assert result.root.type == "workspaceWrite"

    def test_danger_full_access_returns_danger_full_access_policy(self):
        result = _to_sandbox_policy(SandboxMode.danger_full_access)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, DangerFullAccessSandboxPolicy)
        assert result.root.type == "dangerFullAccess"


class TestResolveCodexTurnOverrides:
    @pytest.mark.parametrize("mode,sandbox_mode,approval_str", PRESET_TABLE)
    def test_known_mode_returns_sandbox_policy_and_approval(
        self, mode, sandbox_mode, approval_str,
    ):
        sandbox_policy, approval = resolve_codex_turn_overrides(mode)
        assert isinstance(sandbox_policy, SandboxPolicy)
        # The inner SandboxPolicy variant matches the SandboxMode.
        type_map = {
            SandboxMode.read_only: ReadOnlySandboxPolicy,
            SandboxMode.workspace_write: WorkspaceWriteSandboxPolicy,
            SandboxMode.danger_full_access: DangerFullAccessSandboxPolicy,
        }
        assert isinstance(sandbox_policy.root, type_map[sandbox_mode])
        _assert_approval_matches(approval, approval_str)

    def test_none_falls_back_to_default(self):
        sandbox_policy, approval = resolve_codex_turn_overrides(None)
        # DEFAULT_MODE = "auto" → workspace_write + on-request
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root.value == "on-request"

    def test_unknown_mode_falls_back_to_default(self):
        sandbox_policy, approval = resolve_codex_turn_overrides("bogus")
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root.value == "on-request"
