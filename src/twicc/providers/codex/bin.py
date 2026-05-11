"""
Locate the platform-matching Codex binary shipped inside our wheel.

The build hook (``hatch_build.py``) drops the right binary at
``codex_app_server/_bundled/{codex,codex.exe}`` when the wheel is built;
editable installs can populate it manually via ``python hatch_build.py``.

Kept in its own module — and mirrored by ``claude_code/bin.py`` for the
Claude Code wheel layout — so every codepath that needs the bundled
binary (agent runtime, one-shot SDK turns, the ``twicc codex`` proxy)
imports the same helper, with the same name, from the same shape of
module: ``twicc.providers.<provider>.bin.resolve_bundled_binary()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import codex_app_server

# Resolved path is process-wide constant (wheel layout doesn't change once
# installed), so we cache after the first successful lookup. Misses (binary
# absent) deliberately don't cache — leaves room for an editable install to
# populate the directory mid-process via ``python hatch_build.py``.
_BIN_PATH: Path | None = None


def resolve_bundled_binary() -> Path:
    """Return the path to the codex binary shipped inside our wheel."""
    global _BIN_PATH
    if _BIN_PATH is not None:
        return _BIN_PATH

    bundled_dir = Path(codex_app_server.__file__).resolve().parent / "_bundled"
    bin_name = "codex.exe" if sys.platform == "win32" else "codex"
    bin_path = bundled_dir / bin_name
    if not bin_path.is_file():
        raise FileNotFoundError(
            f"Bundled Codex binary not found at {bin_path}. Did the build "
            "hook run? See hatch_build.py for the install/build path, or run "
            "`python hatch_build.py` to populate it in an editable install.",
        )
    _BIN_PATH = bin_path
    return _BIN_PATH
