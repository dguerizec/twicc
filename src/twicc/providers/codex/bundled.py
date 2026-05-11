"""
Locate the platform-matching Codex binary shipped inside our wheel.

The build hook (``hatch_build.py``) drops the right binary at
``codex_app_server/_bundled/{codex,codex.exe}`` when the wheel is built;
editable installs can populate it manually via ``python hatch_build.py``.

Kept in its own module so both the agent runtime (``agent/manager.py``)
and the one-shot title-suggestion path (``title_suggest.py``) can share
it without one importing the other.
"""

from __future__ import annotations

import sys
from pathlib import Path

import codex_app_server


def resolve_bundled_codex_bin() -> Path:
    """Return the path to the codex binary shipped inside our wheel."""
    bundled_dir = Path(codex_app_server.__file__).resolve().parent / "_bundled"
    bin_name = "codex.exe" if sys.platform == "win32" else "codex"
    bin_path = bundled_dir / bin_name
    if not bin_path.is_file():
        raise FileNotFoundError(
            f"Bundled Codex binary not found at {bin_path}. Did the build "
            "hook run? See hatch_build.py for the install/build path, or run "
            "`python hatch_build.py` to populate it in an editable install.",
        )
    return bin_path
