"""JSON formatting of the drop-request final result.

The ``twicc`` CLI speaks JSON by default on every structured command, so
these helpers always emit JSON through :func:`twicc.cli._output.emit_json`.
There is no text / progress mode anymore: a drop-request command stays
silent until it emits a single final JSON object (the watcher's status
payload, a validation error, a rejection, or a timeout). Errors that are
not part of that payload (heartbeat down, bad arguments) stay on stderr
as plain text in the calling command.
"""

from __future__ import annotations

from twicc.cli._output import emit_json


def emit_validation_errors(errors) -> None:
    emit_json({
        "status": "validation_error",
        "errors": [e._asdict() for e in errors],
    })


# Field projection per result kind. The watcher only writes an id field
# when the underlying service Result populated it (see
# ``_RESULT_ID_FIELDS`` in ``drop_requests_watcher.py``), so we
# dispatch by the id field that actually appears in the status payload.
_SESSION_ID_FIELDS = ("session_id", "provider", "project_id")
_WORKSPACE_ID_FIELDS = ("workspace_id",)
_PROJECT_ID_FIELDS = ("project_id",)


def emit_final(outcome, *, request_uuid: str, timeout: int) -> None:
    if outcome.status in ("created", "sent", "updated", "stopped", "deleted"):
        d = outcome.data
        # Dispatch by which id field is set. ``workspace_id`` is workspace-only;
        # ``session_id`` is session-only; ``project_id`` alone (without
        # ``session_id``) means the result describes a project mutation.
        if "workspace_id" in d:
            id_fields = _WORKSPACE_ID_FIELDS
        elif "session_id" in d:
            id_fields = _SESSION_ID_FIELDS
        else:
            id_fields = _PROJECT_ID_FIELDS

        payload = {"status": outcome.status, "request_uuid": request_uuid}
        for field in id_fields:
            payload[field] = d.get(field)
        emit_json(payload)
    elif outcome.status == "rejected":
        d = outcome.data
        emit_json({
            "status": "rejected",
            "errors": d.get("errors", []),
            "request_uuid": request_uuid,
        })
    elif outcome.status == "failed":
        d = outcome.data
        emit_json({
            "status": "failed",
            "error": d.get("error"),
            "request_uuid": request_uuid,
        })
    else:
        # timeout
        if outcome.received_seen:
            msg = (f"Request was received but server did not respond within "
                   f"{timeout}s. Check server logs.")
        else:
            msg = f"No confirmation from server after {timeout}s."
        emit_json({
            "status": "timeout",
            "received_seen": outcome.received_seen,
            "message": msg,
            "request_uuid": request_uuid,
        })
