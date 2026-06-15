"""Unit tests for the system work-dir auto-approval layer.

A tool approval that touches ONLY the session's own system dirs (its
``artifacts/<id>`` / ``scratch/<id>`` and the orchestration root's shared
``scratch/<root_id>``) is silently auto-approved; anything that reaches one
path outside, or whose footprint cannot be fully enumerated, falls through to
the normal prompt. These are the pure pieces of that decision:

- ``all_targets_within_work_dirs`` — the provider-neutral containment rule.
- ``extract_codex_approval_paths`` / ``auto_approve_response_for`` — Codex.
- ``extract_claude_tool_paths`` — Claude (SDK + hybrid share it).
"""

from __future__ import annotations

import os

import pytest

from twicc.agent.work_dir_autoapprove import all_targets_within_work_dirs
from twicc.providers.claude_code.agent.permissions import extract_claude_tool_paths
from twicc.providers.codex.agent.approvals import (
    auto_approve_response_for,
    extract_codex_approval_paths,
)


@pytest.fixture
def work_dirs(tmp_path):
    """Three real, existing dirs standing in for the session's grants."""
    art = tmp_path / "artifacts" / "S1"
    scr = tmp_path / "scratch" / "S1"
    root = tmp_path / "scratch" / "ROOT"
    other = tmp_path / "scratch" / "OTHER"
    for d in (art, scr, root, other):
        d.mkdir(parents=True)
    return {
        "art": str(art), "scr": str(scr), "root": str(root), "other": str(other),
        "list": [str(art), str(scr), str(root)],
    }


class TestContainment:
    def test_in_scope_each_grant(self, work_dirs):
        wd = work_dirs["list"]
        assert all_targets_within_work_dirs([os.path.join(work_dirs["art"], "f.png")], wd)
        assert all_targets_within_work_dirs([os.path.join(work_dirs["scr"], "a/b.txt")], wd)
        assert all_targets_within_work_dirs([os.path.join(work_dirs["root"], "x")], wd)

    def test_not_yet_created_target_in_scope(self, work_dirs):
        # A fresh Write to a path that does not exist yet still resolves.
        assert all_targets_within_work_dirs(
            [os.path.join(work_dirs["scr"], "deep", "new.txt")], work_dirs["list"],
        )

    def test_other_session_dir_rejected(self, work_dirs):
        assert not all_targets_within_work_dirs(
            [os.path.join(work_dirs["other"], "x")], work_dirs["list"],
        )

    def test_dotdot_escape_rejected(self, work_dirs):
        assert not all_targets_within_work_dirs(
            [os.path.join(work_dirs["scr"], "..", "OTHER", "x")], work_dirs["list"],
        )

    def test_symlink_escape_rejected(self, work_dirs):
        # A symlink planted inside a work dir that points outside resolves out.
        link = os.path.join(work_dirs["scr"], "escape")
        os.symlink(work_dirs["other"], link)
        assert not all_targets_within_work_dirs(
            [os.path.join(link, "x")], work_dirs["list"],
        )

    def test_prefix_sibling_not_matched(self, work_dirs):
        # ``scratch/S1_evil`` must not be treated as inside ``scratch/S1``.
        assert not all_targets_within_work_dirs(
            [work_dirs["scr"] + "_evil/x"], work_dirs["list"],
        )

    def test_mixed_in_and_out_rejected(self, work_dirs):
        assert not all_targets_within_work_dirs(
            [os.path.join(work_dirs["scr"], "a"), os.path.join(work_dirs["other"], "b")],
            work_dirs["list"],
        )

    def test_empty_inputs_reject(self, work_dirs):
        assert not all_targets_within_work_dirs([], work_dirs["list"])
        assert not all_targets_within_work_dirs([os.path.join(work_dirs["scr"], "a")], [])
        assert not all_targets_within_work_dirs([os.path.join(work_dirs["scr"], "a")], None)


class TestExtractCodexApprovalPaths:
    def test_file_change_changes(self, work_dirs):
        p = os.path.join(work_dirs["scr"], "f")
        params = {"_item_payload": {"changes": [{"path": p, "kind": {"type": "add"}, "diff": "."}]}}
        assert extract_codex_approval_paths("item/fileChange/requestApproval", params) == ([p], True)

    def test_file_change_without_payload_unknown(self):
        assert extract_codex_approval_paths(
            "item/fileChange/requestApproval", {"itemId": "x"},
        ) == ([], False)

    def test_file_change_missing_path_unknown(self):
        params = {"_item_payload": {"changes": [{"kind": {"type": "add"}}]}}
        assert extract_codex_approval_paths("item/fileChange/requestApproval", params) == ([], False)

    def test_apply_patch_via_shell_payload(self, work_dirs):
        p = os.path.join(work_dirs["scr"], "f")
        params = {"_item_payload": {
            "command": "apply_patch", "cwd": work_dirs["scr"],
            "commandActions": [{"type": "read", "path": p}],
        }}
        assert extract_codex_approval_paths("item/fileChange/requestApproval", params) == ([p], True)

    def test_command_scoped_reads(self, work_dirs):
        a, b = work_dirs["scr"], os.path.join(work_dirs["art"], "x")
        params = {"command": "ls", "cwd": work_dirs["scr"], "commandActions": [
            {"type": "listFiles", "path": a}, {"type": "read", "path": b},
        ]}
        assert extract_codex_approval_paths(
            "item/commandExecution/requestApproval", params,
        ) == ([a, b], True)

    def test_command_relative_path_resolved_against_cwd(self, work_dirs):
        params = {"command": "cat", "cwd": work_dirs["scr"], "commandActions": [
            {"type": "read", "path": "note.txt"},
        ]}
        assert extract_codex_approval_paths(
            "item/commandExecution/requestApproval", params,
        ) == ([os.path.join(work_dirs["scr"], "note.txt")], True)

    def test_command_with_unknown_action_bails(self, work_dirs):
        params = {"command": "x", "cwd": work_dirs["scr"], "commandActions": [
            {"type": "read", "path": work_dirs["scr"]}, {"type": "unknown", "command": "rm"},
        ]}
        assert extract_codex_approval_paths(
            "item/commandExecution/requestApproval", params,
        ) == ([], False)

    def test_command_network_null_bails(self, work_dirs):
        params = {"command": None, "commandActions": [{"type": "read", "path": work_dirs["scr"]}]}
        assert extract_codex_approval_paths(
            "item/commandExecution/requestApproval", params,
        ) == ([], False)

    def test_command_no_actions_bails(self, work_dirs):
        params = {"command": "x", "cwd": work_dirs["scr"], "commandActions": []}
        assert extract_codex_approval_paths(
            "item/commandExecution/requestApproval", params,
        ) == ([], False)

    def test_permissions_never(self):
        assert extract_codex_approval_paths(
            "item/permissions/requestApproval", {"cwd": "/x"},
        ) == ([], False)

    def test_empty_params(self):
        assert extract_codex_approval_paths("item/fileChange/requestApproval", None) == ([], False)


class TestAutoApproveResponseFor:
    @pytest.mark.parametrize("method", [
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    ])
    def test_accept_for_command_and_file(self, method):
        assert auto_approve_response_for(method) == {"decision": "accept"}

    def test_permissions_raises(self):
        with pytest.raises(ValueError):
            auto_approve_response_for("item/permissions/requestApproval")

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError):
            auto_approve_response_for("item/tool/call")


class TestExtractClaudeToolPaths:
    @pytest.mark.parametrize("tool,key", [
        ("Read", "file_path"), ("Write", "file_path"), ("Edit", "file_path"),
        ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"),
    ])
    def test_exact_path_tools(self, tool, key):
        assert extract_claude_tool_paths(tool, {key: "/a/b"}) == (["/a/b"], True)

    def test_file_tool_without_path_unknown(self):
        assert extract_claude_tool_paths("Write", {}) == ([], False)

    def test_bash_with_blocked_path(self):
        assert extract_claude_tool_paths("Bash", {"command": "x"}, "/a/b") == (["/a/b"], True)

    def test_bash_without_blocked_path_unknown(self):
        assert extract_claude_tool_paths("Bash", {"command": "x"}, None) == ([], False)

    def test_non_path_tool_unknown(self):
        assert extract_claude_tool_paths("ExitPlanMode", {"plan": "..."}) == ([], False)
