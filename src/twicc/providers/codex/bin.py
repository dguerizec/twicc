"""Locate the Codex CLI binary installed via ``openai-codex-cli-bin``.

The binary ships in the ``openai-codex-cli-bin`` package on PyPI (manylinux
on Linux, macOS, Windows). The package exposes ``bundled_codex_path`` which
returns a ``Path`` to the platform-specific binary.

Kept in its own module — and mirrored by ``claude_code/bin.py`` for the
Claude Code wheel layout — so every codepath that needs the bundled
binary (agent runtime, one-shot SDK turns, the ``twicc codex`` proxy)
imports the same helper, with the same name, from the same shape of
module: ``twicc.providers.<provider>.bin.resolve_bundled_binary()``.
"""

from __future__ import annotations

from pathlib import Path

from codex_cli_bin import bundled_codex_path

# Resolved path is process-wide constant, so we cache after the first
# successful lookup.
_BIN_PATH: Path | None = None


def resolve_bundled_binary() -> Path:
    """Return the path to the Codex CLI binary shipped via ``openai-codex-cli-bin``."""
    global _BIN_PATH
    if _BIN_PATH is None:
        _BIN_PATH = bundled_codex_path()
    return _BIN_PATH
