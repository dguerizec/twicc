"""Proxy to the Codex CLI provided by openai-codex-cli-bin."""

import os
import sys

from twicc.providers.codex.bin import resolve_bundled_binary


def main(args: list[str]) -> None:
    """Replace the current process with the bundled Codex CLI."""
    try:
        binary = str(resolve_bundled_binary())
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    os.execvp(binary, [binary, *args])
