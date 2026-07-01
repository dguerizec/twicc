"""Tests for saved-workflow discovery — ``_scan_workflows_dir``.

This is the filesystem-facing layer on top of ``extract_workflow_meta``
(unit-tested in ``test_workflow_meta_extract.py``). It covers:

- a valid workflow becomes a ``DiscoveredCommand`` with ``is_workflow=True``;
- **resilience**: a file whose meta can't be parsed is skipped silently;
- name fallback (``meta.name`` → filename stem) and description fallback;
- flat scan (no recursion) and ``.js``-only filtering.
"""

from __future__ import annotations

from pathlib import Path

from twicc.providers.claude_code.commands import (
    DiscoveredCommand,
    _scan_workflows_dir,
)

VALID = """export const meta = {
  name: 'my-workflow',
  description: 'Does a thing.',
  phases: [{ title: 'A', detail: 'x' }],
}
const X = 1
"""


def _write(directory: Path, name: str, content: str) -> None:
    (directory / name).write_text(content, encoding="utf-8")


def test_valid_workflow_discovered(tmp_path: Path) -> None:
    _write(tmp_path, "my-workflow.js", VALID)
    (cmd,) = _scan_workflows_dir(tmp_path)
    assert cmd == DiscoveredCommand(
        name="my-workflow",
        plugin_name=None,
        description="Does a thing.",
        argument_hint=None,
        is_workflow=True,
    )


def test_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert _scan_workflows_dir(tmp_path / "does-not-exist") == []


def test_unparseable_meta_skipped(tmp_path: Path) -> None:
    """A .js file with no parseable meta is ignored, others still surface."""
    _write(tmp_path, "bad.js", "const x = 1\nexport default x\n")
    _write(tmp_path, "good.js", VALID)
    assert [c.name for c in _scan_workflows_dir(tmp_path)] == ["my-workflow"]


def test_name_falls_back_to_filename(tmp_path: Path) -> None:
    _write(tmp_path, "fallback-name.js", "export const meta = { description: 'no name field' }")
    (cmd,) = _scan_workflows_dir(tmp_path)
    assert cmd.name == "fallback-name"
    assert cmd.description == "no name field"
    assert cmd.is_workflow is True


def test_blank_name_falls_back_to_filename(tmp_path: Path) -> None:
    _write(tmp_path, "realstem.js", "export const meta = { name: '   ', description: 'blank name' }")
    (cmd,) = _scan_workflows_dir(tmp_path)
    assert cmd.name == "realstem"


def test_description_falls_back_to_workflow(tmp_path: Path) -> None:
    _write(tmp_path, "nodesc.js", "export const meta = { name: 'nodesc' }")
    (cmd,) = _scan_workflows_dir(tmp_path)
    assert cmd.description == "Workflow"


def test_non_js_files_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "readme.md", "# not a workflow")
    _write(tmp_path, "notes.txt", "nope")
    assert _scan_workflows_dir(tmp_path) == []


def test_subdirectories_not_recursed(tmp_path: Path) -> None:
    """Saved workflows live directly under workflows/ — nested .js is ignored."""
    sub = tmp_path / "nested"
    sub.mkdir()
    _write(sub, "deep.js", VALID)
    assert _scan_workflows_dir(tmp_path) == []


def test_results_ordered_by_filename(tmp_path: Path) -> None:
    """Ordering is by filename (glob sort), not by meta.name."""
    _write(tmp_path, "a.js", "export const meta = { name: 'zzz', description: 'x' }")
    _write(tmp_path, "b.js", "export const meta = { name: 'aaa', description: 'y' }")
    assert [c.name for c in _scan_workflows_dir(tmp_path)] == ["zzz", "aaa"]


def test_replace_preserves_is_workflow(tmp_path: Path) -> None:
    """The plugin scan tags rows via ``_replace(plugin_name=...)`` — is_workflow survives."""
    _write(tmp_path, "w.js", VALID)
    (cmd,) = _scan_workflows_dir(tmp_path)
    tagged = cmd._replace(plugin_name="superpowers")
    assert tagged.is_workflow is True
    assert tagged.plugin_name == "superpowers"
