"""Settings-specific final JSON emitter for drop-request write commands.

``emit_settings_final`` is the settings analogue of
:func:`twicc.cli._drop_request.output.emit_final`.  The generic
:func:`~twicc.cli._drop_request.output.build_final` dispatches by id
field and would emit ``project_id: null`` for a settings result (which
carries no id).  This module instead builds the correct settings-shaped
payload and returns the exit code so callers can call
``raise typer.Exit(code)`` consistently.

Exit-code mapping (matches every other write command):
  0 — updated
  3 — rejected
  4 — failed
  5 — timeout
"""

from __future__ import annotations

from twicc.cli._output import emit_json


def emit_settings_final(outcome, *, request_uuid: str, timeout: int) -> int:
    """Emit the final JSON for a settings drop-request and return the exit code.

    Parameters
    ----------
    outcome:
        :class:`~twicc.cli._drop_request.polling.PollOutcome` returned by
        :func:`~twicc.cli._drop_request.polling.poll_status`.
    request_uuid:
        UUID string from the :class:`~twicc.cli._drop_request.drop_file.DropFile`.
    timeout:
        The poll timeout (seconds), used in the timeout message.

    Returns
    -------
    int
        Exit code: 0 updated, 3 rejected, 4 failed, 5 timeout.
    """
    if outcome.status == "updated":
        d = outcome.data or {}
        payload: dict = {"status": "updated", "request_uuid": request_uuid}
        for k in ("corrections", "tested", "test_results"):
            if k in d:
                payload[k] = d[k]
        emit_json(payload)
        return 0

    if outcome.status == "rejected":
        d = outcome.data or {}
        emit_json({
            "status": "rejected",
            "errors": d.get("errors", []),
            "request_uuid": request_uuid,
        })
        return 3

    if outcome.status == "failed":
        d = outcome.data or {}
        emit_json({
            "status": "failed",
            "error": d.get("error"),
            "request_uuid": request_uuid,
        })
        return 4

    # timeout
    if outcome.received_seen:
        msg = (
            f"Request was received but server did not respond within "
            f"{timeout}s. Check server logs."
        )
    else:
        msg = f"No confirmation from server after {timeout}s."
    emit_json({
        "status": "timeout",
        "received_seen": outcome.received_seen,
        "message": msg,
        "request_uuid": request_uuid,
    })
    return 5
