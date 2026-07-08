"""``twicc share create/update/revoke/unrevoke/delete/propagate`` — drop-request
glue (server resolves + broadcasts). Same plumbing as artifacts_mutation._run_drop."""

from __future__ import annotations

from twicc.cli.artifacts_mutation import _run_drop  # reuse the heartbeat + poll helper


def run_create_session(*, session_id: str, label: str, password: str | None,
                       expires_at: str | None, mode: str, options: dict, timeout: int) -> None:
    _run_drop(
        {"kind_target": "session", "session_id": session_id, "label": label,
         "password": password, "expires_at": expires_at,
         "options": {**options, "mode": mode}},
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_create_artifact(*, bookmark_id: int, label: str, password: str | None,
                        expires_at: str | None, options: dict, timeout: int) -> None:
    _run_drop(
        {"kind_target": "artifact", "bookmark_id": bookmark_id, "label": label,
         "password": password, "expires_at": expires_at, "options": options},
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_update(*, share_id: str, fields: dict, timeout: int) -> None:
    _run_drop({"share_id": share_id, "fields": fields}, kind="share:update",
              success_status="updated", timeout=timeout)


def run_simple(*, share_id: str, kind: str, success: str, timeout: int) -> None:
    _run_drop({"share_id": share_id}, kind=kind, success_status=success, timeout=timeout)
