"""TwiCC plugin for Claude Code.

Provides skills and commands that enhance Claude Code sessions started through TwiCC,
giving Claude access to TwiCC-specific capabilities like session search.
"""

from pathlib import Path


def get_plugin_dir() -> Path:
    """Return the path to the TwiCC plugin directory.

    This is the directory that contains `.claude-plugin/plugin.json`
    and should be passed to ClaudeAgentOptions as a plugin path.
    """
    return Path(__file__).parent
