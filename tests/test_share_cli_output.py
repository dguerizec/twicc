"""build_final dispatch for share results (agent-sharing design §8): without a
share branch the formatter falls through to the project projection and the
share_id is lost — the two-call create→show flow would be impossible."""

from types import SimpleNamespace

import orjson
from typer.testing import CliRunner

from twicc.cli import app
from twicc.cli._drop_request.output import build_final


def _outcome(status, data):
    return SimpleNamespace(status=status, data=data, received_seen=True)


def test_share_create_result_carries_share_id():
    final = build_final(
        _outcome("created", {"status": "created", "share_id": "shr_ab12cd34"}),
        request_uuid="u-1", timeout=30,
    )
    assert final == {"status": "created", "share_id": "shr_ab12cd34", "request_uuid": "u-1"}


def test_share_update_and_delete_results_carry_share_id():
    for status in ("updated", "deleted"):
        final = build_final(
            _outcome(status, {"status": status, "share_id": "shr_x"}),
            request_uuid="u-2", timeout=30,
        )
        assert final["share_id"] == "shr_x"
        assert "project_id" not in final


def test_other_families_unchanged():
    final = build_final(
        _outcome("updated", {"status": "updated", "bookmark_id": 3,
                             "session_id": "s1", "project_id": "p1"}),
        request_uuid="u-3", timeout=30,
    )
    assert final["bookmark_id"] == 3
    assert "share_id" not in final


def test_real_cli_create_surface_carries_only_result_ids(monkeypatch):
    """Cross the Typer command and real _run_drop/emit_final path."""
    from twicc.cli._drop_request import transport

    class Submission:
        request_uuid = "u-cli"

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: Submission())
    monkeypatch.setattr(
        transport,
        "wait",
        lambda submission, *, timeout_seconds: _outcome(
            "created", {"status": "created", "share_id": "shr_cli"}),
    )
    # Task 9 wraps this command with caller discovery. Keep this Task 6
    # boundary test on the human path and independent of the ORM.
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )
    result = CliRunner().invoke(app, ["share", "create", "session", "s1"])
    assert result.exit_code == 0, result.output
    assert orjson.loads(result.stdout) == {
        "status": "created", "share_id": "shr_cli", "request_uuid": "u-cli",
    }
