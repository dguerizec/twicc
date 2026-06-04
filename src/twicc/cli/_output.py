"""Centralized JSON output for the CLI.

Every command that prints a structured result to stdout goes through
:func:`emit_json`. This is the single place that decides the on-the-wire
JSON shape (indented, UTF-8, trailing newline) so the whole CLI stays
uniform: the ``twicc`` CLI speaks JSON by default on every structured
command — there is no text/``--json`` toggle to get wrong. Human-only
commands (``password``, the ``claude`` / ``codex`` passthroughs, ``run``)
print their own text and never call this.

Errors stay on stderr as plain text (validation messages, "not found",
parser errors) — only the structured success payload is JSON on stdout.
"""

from __future__ import annotations

import sys

import orjson


def emit_json(payload) -> None:
    """Write ``payload`` to stdout as indented UTF-8 JSON with a trailing newline."""
    sys.stdout.buffer.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
