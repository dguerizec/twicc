"""Text and JSON formatting of progress and final result."""

from __future__ import annotations

import sys

import orjson
import typer


def emit_progress(line: str, *, json_output: bool) -> None:
    if not json_output:
        typer.echo(line)


def _format_bytes(n: int) -> str:
    """Render ``n`` bytes as a compact KB/MB string."""
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def emit_attachment_summary(summary, *, json_output: bool) -> None:
    """Print one line per attachment after validation succeeded.

    In text mode, lists each file's basename, kind, size, and (for
    images) the original vs. final dimensions when a resize happened.
    Suppressed entirely in JSON mode — the final ``created`` event
    carries enough info for scripted consumers.
    """
    if json_output or not summary:
        return
    import os as _os
    for item in summary:
        name = _os.path.basename(item.path)
        size_str = _format_bytes(item.final_size)
        if item.kind == "image":
            assert item.original_dim is not None and item.final_dim is not None
            ow, oh = item.original_dim
            fw, fh = item.final_dim
            if item.resized:
                orig_str = _format_bytes(item.original_size)
                typer.echo(
                    f"  • {name} — image ({item.mime}), "
                    f"resized {ow}x{oh} → {fw}x{fh}, {orig_str} → {size_str}"
                )
            else:
                typer.echo(
                    f"  • {name} — image ({item.mime}), {fw}x{fh}, {size_str}"
                )
        elif item.kind == "document":
            typer.echo(f"  • {name} — document ({item.mime}), {size_str}")
        else:
            typer.echo(f"  • {name} — text ({item.mime}), {size_str}")


def emit_validation_errors(errors, *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(orjson.dumps({
            "status": "validation_error",
            "errors": [e._asdict() for e in errors],
        }).decode())
        sys.stdout.write("\n")
    else:
        typer.echo("✗ Validation error:", err=True)
        for e in errors:
            typer.echo(f"  - {e.field}: {e.message}", err=True)


def emit_final(outcome, *, request_uuid: str, json_output: bool, timeout: int) -> None:
    if outcome.status in ("created", "sent", "updated", "stopped"):
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": outcome.status,
                "session_id": d.get("session_id"),
                "provider": d.get("provider"),
                "project_id": d.get("project_id"),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            if outcome.status == "created":
                typer.echo(f"✓ Session created: {d.get('session_id')}")
            elif outcome.status == "sent":
                typer.echo(f"✓ Message sent to session: {d.get('session_id')}")
            elif outcome.status == "updated":
                typer.echo(f"✓ Session updated: {d.get('session_id')}")
            else:
                typer.echo(f"✓ Process stopped for session: {d.get('session_id')}")
    elif outcome.status == "rejected":
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "rejected",
                "errors": d.get("errors", []),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo("✗ Rejected by server:", err=True)
            for e in d.get("errors", []):
                typer.echo(f"  - {e.get('code')}: {e.get('message')}", err=True)
    elif outcome.status == "failed":
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "failed",
                "error": d.get("error"),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo(f"✗ Unexpected server error: {d.get('error')}", err=True)
    else:
        # timeout
        if outcome.received_seen:
            msg = (f"Request was received but server did not respond within "
                   f"{timeout}s. Check server logs.")
        else:
            msg = f"No confirmation from server after {timeout}s."
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "timeout",
                "received_seen": outcome.received_seen,
                "message": msg,
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo(f"✗ {msg}", err=True)
