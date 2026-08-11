"""Payloads the `twicc share` CLI produces (agent-sharing design §7.2): the
tri-state --live/--frozen flag must let `options.mode` be ABSENT, or the
server-side frozen default for agents can never fire."""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def captured_drop(monkeypatch):
    calls = []

    def fake_run_drop(payload, *, kind, success_status, timeout):
        calls.append({"payload": payload, "kind": kind})

    monkeypatch.setattr("twicc.cli.share_mutation._run_drop", fake_run_drop)
    return calls


def _invoke(args):
    from twicc.cli import app
    return runner.invoke(app, args)


def test_no_flag_omits_mode(captured_drop):
    result = _invoke(["share", "create", "session", "sess-1"])
    assert result.exit_code == 0
    options = captured_drop[0]["payload"]["options"]
    assert "mode" not in options


def test_explicit_live_and_frozen(captured_drop):
    _invoke(["share", "create", "session", "sess-1", "--live"])
    _invoke(["share", "create", "session", "sess-1", "--frozen"])
    assert captured_drop[0]["payload"]["options"]["mode"] == "live"
    assert captured_drop[1]["payload"]["options"]["mode"] == "snapshot"
