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


# Field projection per result kind. The watcher only writes an id field
# when the underlying service Result populated it (see
# ``_RESULT_ID_FIELDS`` in ``drop_requests_watcher.py``), so we
# dispatch by the id field that actually appears in the status payload.
_SESSION_ID_FIELDS = ("session_id", "provider", "project_id")
_WORKSPACE_ID_FIELDS = ("workspace_id",)
_PROJECT_ID_FIELDS = ("project_id",)


def emit_final(outcome, *, request_uuid: str, json_output: bool, timeout: int) -> None:
    if outcome.status in ("created", "sent", "updated", "stopped", "deleted"):
        d = outcome.data
        # Dispatch by which id field is set. ``workspace_id`` is workspace-only;
        # ``session_id`` is session-only; ``project_id`` alone (without
        # ``session_id``) means the result describes a project mutation.
        if "workspace_id" in d:
            id_fields = _WORKSPACE_ID_FIELDS
            subject = "Workspace"
            identifier = d.get("workspace_id")
        elif "session_id" in d:
            id_fields = _SESSION_ID_FIELDS
            subject = "Session"
            identifier = d.get("session_id")
        else:
            id_fields = _PROJECT_ID_FIELDS
            subject = "Project"
            identifier = d.get("project_id")

        if json_output:
            payload = {"status": outcome.status, "request_uuid": request_uuid}
            for field in id_fields:
                payload[field] = d.get(field)
            sys.stdout.write(orjson.dumps(payload).decode() + "\n")
        else:
            if outcome.status == "created":
                typer.echo(f"✓ {subject} created: {identifier}")
            elif outcome.status == "sent":
                typer.echo(f"✓ Message sent to session: {identifier}")
            elif outcome.status == "updated":
                typer.echo(f"✓ {subject} updated: {identifier}")
            elif outcome.status == "deleted":
                typer.echo(f"✓ {subject} deleted: {identifier}")
            else:  # stopped
                typer.echo(f"✓ Process stopped for session: {identifier}")
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
