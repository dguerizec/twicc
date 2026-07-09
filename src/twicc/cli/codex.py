"""Proxy to the Codex CLI runtime downloaded from GitHub Releases.

A standalone ``twicc codex`` invocation runs as its own process and
``execvp``s straight into the bundled ``codex`` binary, so the backend's
background pre-download never runs for it. It therefore ensures the runtime
itself — downloading it once (blocking) if the cache is cold — before handing
off.
"""

import os
import sys

from twicc.providers.codex.bin import resolve_bundled_binary
from twicc.providers.codex.runtime import CodexRuntimeError, ensure_codex_runtime_sync


def main(args: list[str]) -> None:
    """Replace the current process with the bundled Codex CLI."""
    try:
        ensure_codex_runtime_sync()
        binary = str(resolve_bundled_binary())
    except (CodexRuntimeError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    os.execvp(binary, [binary, *args])
