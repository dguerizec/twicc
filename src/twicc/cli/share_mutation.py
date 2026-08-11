"""``twicc share create/update/revoke/unrevoke/delete/propagate`` — drop-request
glue (server resolves + broadcasts). Same plumbing as artifacts_mutation._run_drop."""

from __future__ import annotations

from twicc.cli.artifacts_mutation import _run_drop  # reuse the heartbeat + poll helper


def _with_caller(payload: dict) -> dict:
    """Stamp the resolved caller session id (design §5.1) — best-effort
    identity, same pattern as peer-send's origin_session_id. The server-side
    gate treats a missing key as a human caller; this is a guardrail, not a
    security boundary (§5.2)."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    if current is not None:
        payload["caller_session_id"] = current.id
    return payload


def run_create_session(*, session_id: str, label: str, password: str | None,
                       expires_at: str | None, mode: str | None, options: dict,
                       timeout: int) -> None:
    opts = dict(options)
    if mode is not None:
        opts["mode"] = mode
    _run_drop(
        _with_caller(
            {"kind_target": "session", "session_id": session_id, "label": label,
             "password": password, "expires_at": expires_at, "options": opts},
        ),
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_create_artifact(*, bookmark_id: int, label: str, password: str | None,
                        expires_at: str | None, options: dict, timeout: int) -> None:
    _run_drop(
        _with_caller(
            {"kind_target": "artifact", "bookmark_id": bookmark_id, "label": label,
             "password": password, "expires_at": expires_at, "options": options},
        ),
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_update(*, share_id: str, fields: dict, timeout: int) -> None:
    _run_drop(_with_caller({"share_id": share_id, "fields": fields}), kind="share:update",
              success_status="updated", timeout=timeout)


def run_simple(*, share_id: str, kind: str, success: str, timeout: int) -> None:
    _run_drop(_with_caller({"share_id": share_id}), kind=kind, success_status=success, timeout=timeout)
