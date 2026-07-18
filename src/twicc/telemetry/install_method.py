"""Best-effort install-method detection (design §3.1).

Never guesses: unrecognized layouts return "other". Signals are path
signatures on ``sys.executable`` plus a ``.git`` probe for editable
checkouts — the same family of signals as ``_resolve_twicc_launch_prefix()``
(``src/twicc/settings.py``), which distinguishes launch modes for another
purpose (building the re-invocation command).
"""

from __future__ import annotations

import sys
from pathlib import Path

import twicc


def _is_git_checkout() -> bool:
    # Editable install / dev checkout: a .git directory above the package
    # source (src/twicc/ -> repo root).
    # Best-effort limitation (accepted, not fixing): this can over-report
    # git-dev if twicc happens to be installed into a venv that itself sits
    # nested inside an unrelated git repo — the walk stops at the first
    # .git or pyproject.toml it finds, whichever is an ancestor of the
    # package source, without verifying it actually IS the twicc repo.
    package_root = Path(twicc.__file__).resolve().parent
    for parent in package_root.parents:
        if (parent / ".git").exists():
            return True
        if (parent / "pyproject.toml").exists():
            break
    return False


def _in_virtualenv(exe: Path) -> bool:
    """Whether ``exe`` looks like a venv interpreter.

    Hybrid: prefer the real filesystem signal (a ``pyvenv.cfg`` at the venv
    root, i.e. ``exe.parent.parent`` for a ``<venv>/bin/python`` layout) so
    production venvs are detected correctly even when their directory name
    doesn't mention "venv" (e.g. ``/opt/app/bin/python``). This stats a path
    derived from the passed-in ``exe``, never ``sys.prefix``/
    ``sys.base_prefix`` of the current process, so it stays accurate for an
    arbitrary ``exe`` under test (unlike ``sys.prefix != sys.base_prefix``,
    which reflects THIS process — under ``uv run`` that's always inside a
    venv, which would make every path look like one).

    Falls back to a path-signature check (any path segment containing
    "venv") when there's no such file on disk — the case for fake
    test-supplied paths, and a reasonable fallback for real ones too.
    """
    if (exe.parent.parent / "pyvenv.cfg").exists():
        return True
    return any("venv" in part for part in exe.parts)


def detect_install_method() -> str:
    if _is_git_checkout():
        return "git-dev"
    exe = Path(sys.executable)
    exe_str = str(exe)
    if "/uv/tools/" in exe_str:
        return "uv-tool"
    if "/.cache/uv/" in exe_str or "/uv/cache/" in exe_str:
        return "uvx"
    if "/pipx/venvs/" in exe_str:
        return "pipx"
    if _in_virtualenv(exe):
        return "pip"
    return "other"
