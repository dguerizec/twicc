"""Centralized JSON output (and error) for the CLI.

Every structured command prints its result through :func:`emit_json` and
reports failures through :func:`emit_error`. These two functions are the
single output choke points for the whole CLI, which keeps the on-the-wire
shape uniform (indented UTF-8 JSON on stdout; plain text on stderr).

They are also the seam the in-process RPC layer hooks into: when an API
caller sets the :data:`_capture` ContextVar to a :class:`_Sink`, the result
and the error message are captured into the sink instead of being written
to stdout/stderr. ContextVars are isolated per-thread and per-asyncio-task
and propagate across ``asyncio.to_thread``, so concurrent /rpc/ invocations
never clash on a global stream. When the ContextVar is unset (normal CLI
use), behaviour is exactly as before.

Human-only commands (``password``, the ``claude`` / ``codex`` passthroughs,
``run``) print their own text and never call these.
"""

from __future__ import annotations

import contextvars
import sys

import orjson
import typer


class _Sink:
    """Capture target for one in-process invocation."""

    __slots__ = ("error", "result")

    def __init__(self) -> None:
        self.result = None
        self.error: str | None = None


_capture: contextvars.ContextVar[_Sink | None] = contextvars.ContextVar(
    "emit_capture", default=None
)


def in_api_mode() -> bool:
    """True when running under the in-process RPC invoker (a capture sink is set).

    Used to reject CWD-relative inputs over the API, where the caller has no
    working directory the server could resolve a relative path against.
    """
    return _capture.get() is not None


def emit_json(payload) -> None:
    """Emit ``payload`` as the command's structured result.

    Normal CLI use: indented UTF-8 JSON to stdout with a trailing newline.
    API mode (capture set): stored on the sink, nothing written.
    """
    sink = _capture.get()
    if sink is not None:
        sink.result = payload
        return
    sys.stdout.buffer.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def emit_error(message: str, *, code: int = 1) -> None:
    """Emit a command failure and stop the command. Always raises ``typer.Exit``.

    Normal CLI use: ``message`` to stderr, then exit with ``code``.
    API mode (capture set): ``message`` stored on the sink, then ``typer.Exit``
    so the invoker can read both the message and the code.
    """
    sink = _capture.get()
    if sink is not None:
        sink.error = message
        raise typer.Exit(code)
    typer.echo(message, err=True)
    raise typer.Exit(code)
