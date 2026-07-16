"""Unit tests for the Codex permission_mode preset mapping.

Six wire-value presets translate to sandbox, approval-policy, and approval-reviewer
controls for ``thread_start`` / ``thread_resume`` and their per-turn equivalents.
Both surfaces are exercised here so any future regression in either path is
caught immediately.
"""

from __future__ import annotations

import pytest

from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
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
from twicc.providers.codex.constants import AGENT_SETTINGS_ALIASES, UNTRUSTED_PERMISSION_MODES
from twicc.providers.codex.helpers import AGENT_SETTINGS_CHOICES


# (wire_mode, sandbox_mode, approval_policy_expectation, reviewer)
# The expectation is the wire string for the plain variants, or "granular" for
# yolo (a GranularAskForApproval object — see _assert_approval_matches).
PRESET_TABLE: list[tuple[str, SandboxMode, str, ApprovalsReviewer]] = [
    ("read_only", SandboxMode.read_only, "on-request", ApprovalsReviewer.user),
    ("strict", SandboxMode.read_only, "never", ApprovalsReviewer.user),
    ("auto", SandboxMode.workspace_write, "on-request", ApprovalsReviewer.user),
    ("autonomous", SandboxMode.workspace_write, "never", ApprovalsReviewer.user),
    ("auto_review", SandboxMode.workspace_write, "on-request", ApprovalsReviewer.auto_review),
    ("yolo", SandboxMode.danger_full_access, "granular", ApprovalsReviewer.user),
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
    def test_preset_map_has_exactly_6_entries(self):
        assert len(_PRESET_MAP) == 6

    def test_default_mode_is_auto(self):
        assert DEFAULT_MODE == "auto"

    def test_default_mode_is_present_in_preset_map(self):
        assert DEFAULT_MODE in _PRESET_MAP

    def test_auto_review_is_exposed_and_allowed_for_untrusted_projects(self):
        assert "auto_review" in AGENT_SETTINGS_CHOICES["permission_mode"]
        assert "auto_review" in UNTRUSTED_PERMISSION_MODES
        assert AGENT_SETTINGS_ALIASES["permission_mode_if_untrusted"]["max"] == "auto_review"


class TestResolveCodexPolicy:
    @pytest.mark.parametrize("mode,sandbox,approval_str,reviewer", PRESET_TABLE)
    def test_known_mode_returns_expected_policy(self, mode, sandbox, approval_str, reviewer):
        result_sandbox, result_approval, result_reviewer = resolve_codex_policy(mode)
        assert result_sandbox is sandbox
        _assert_approval_matches(result_approval, approval_str)
        assert result_reviewer is reviewer

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
        roots = ["/data/artifacts/session", "/data/scratch/session"]
        result = _to_sandbox_policy(SandboxMode.workspace_write, roots)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, WorkspaceWriteSandboxPolicy)
        assert result.root.type == "workspaceWrite"
        assert result.root.network_access is False
        assert [path.root for path in result.root.writable_roots] == roots

    def test_workspace_write_can_enable_network_access(self):
        result = _to_sandbox_policy(
            SandboxMode.workspace_write,
            network_access=True,
        )
        assert isinstance(result.root, WorkspaceWriteSandboxPolicy)
        assert result.root.network_access is True

    def test_danger_full_access_returns_danger_full_access_policy(self):
        result = _to_sandbox_policy(SandboxMode.danger_full_access)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, DangerFullAccessSandboxPolicy)
        assert result.root.type == "dangerFullAccess"


class TestResolveCodexTurnOverrides:
    @pytest.mark.parametrize("mode,sandbox_mode,approval_str,reviewer", PRESET_TABLE)
    def test_known_mode_returns_sandbox_policy_approval_and_reviewer(
        self, mode, sandbox_mode, approval_str, reviewer,
    ):
        sandbox_policy, approval, result_reviewer = resolve_codex_turn_overrides(mode)
        assert isinstance(sandbox_policy, SandboxPolicy)
        # The inner SandboxPolicy variant matches the SandboxMode.
        type_map = {
            SandboxMode.read_only: ReadOnlySandboxPolicy,
            SandboxMode.workspace_write: WorkspaceWriteSandboxPolicy,
            SandboxMode.danger_full_access: DangerFullAccessSandboxPolicy,
        }
        assert isinstance(sandbox_policy.root, type_map[sandbox_mode])
        _assert_approval_matches(approval, approval_str)
        assert result_reviewer is reviewer

    def test_none_falls_back_to_default(self):
        sandbox_policy, approval, reviewer = resolve_codex_turn_overrides(None)
        # DEFAULT_MODE = "auto" → workspace_write + on-request
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root.value == "on-request"
        assert reviewer is ApprovalsReviewer.user

    def test_unknown_mode_falls_back_to_default(self):
        sandbox_policy, approval, reviewer = resolve_codex_turn_overrides("bogus")
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root.value == "on-request"
        assert reviewer is ApprovalsReviewer.user

    def test_only_auto_review_enables_workspace_network(self):
        auto_policy, _, _ = resolve_codex_turn_overrides("auto")
        review_policy, _, _ = resolve_codex_turn_overrides("auto_review")
        autonomous_policy, _, _ = resolve_codex_turn_overrides("autonomous")

        assert auto_policy.root.network_access is False
        assert review_policy.root.network_access is True
        assert autonomous_policy.root.network_access is False
