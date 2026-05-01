"""Proxy to the Claude Code CLI bundled in claude-agent-sdk."""

import os
import sys
from importlib.resources import files
from pathlib import Path


def get_claude_binary() -> Path:
    """Locate the Claude Code ``claude`` binary shipped inside ``claude_agent_sdk/_bundled/``."""
    binary = Path(str(files("claude_agent_sdk"))) / "_bundled" / "claude"
    if not binary.is_file():
        print(f"Error: Claude binary not found at {binary}", file=sys.stderr)
        raise SystemExit(1)
    return binary


def main(args: list[str]) -> None:
    """Replace the current process with the bundled Claude Code CLI."""
    binary = str(get_claude_binary())
    os.execvp(binary, [binary, *args])
