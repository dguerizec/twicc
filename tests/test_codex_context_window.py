"""Tests for the per-model Codex context window (272K pre-5.6, 372K GPT-5.6).

The window is a fixed property of the model (``CodexModelExtra.context_window``),
not a user choice: ``enforce_agent_settings_consistency`` pins ``context_max``
to the resolved model's window in both directions, the synced defaults are
re-clamped against the default model, and ``extract_runtime_fields`` recovers
the same values from the JSONL ``task_started`` lines (published as 95% of the
nominal input window).
"""

import pytest

import twicc.synced_settings as ss
from twicc.providers.codex.helpers import CodexHelpers
from twicc.providers.helpers import AgentSettings


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """Isolate synced settings so the default-model fallback is deterministic."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.fixture
def helpers():
    return CodexHelpers()


# ─── selected_model_context_window ──────────────────────────────────────────

@pytest.mark.parametrize(
    ("selected_model", "expected"),
    [
        ("gpt-sol", 372_000),
        ("gpt-terra", 372_000),
        ("gpt-luna", 372_000),
        ("gpt", 272_000),        # latest of family gpt = gpt-5.5
        ("gpt-5.4", 272_000),
        ("gpt-mini", 272_000),
    ],
)
def test_selected_model_context_window(helpers, selected_model, expected):
    assert helpers.selected_model_context_window(selected_model) == expected


def test_context_window_unknown_model_falls_back_to_default(helpers, temp_settings):
    # Synced default model is gpt-terra (SYNCED_SETTINGS_DEFAULTS) -> 372K.
    assert helpers.selected_model_context_window("no-such-model") == 372_000
    assert helpers.selected_model_context_window(None) == 372_000


# ─── enforce_agent_settings_consistency ─────────────────────────────────────

def test_enforce_pins_window_up_for_56_model(helpers):
    s = AgentSettings(selected_model="gpt-terra", context_max=272_000)
    assert helpers.enforce_agent_settings_consistency(s).context_max == 372_000


def test_enforce_pins_window_down_for_pre56_model(helpers):
    # Bidirectional, unlike Claude's 1M cap: a 372K value on a pre-5.6 model
    # is replaced too.
    s = AgentSettings(selected_model="gpt", context_max=372_000)
    assert helpers.enforce_agent_settings_consistency(s).context_max == 272_000


def test_enforce_leaves_none_context_max_untouched(helpers):
    s = AgentSettings(selected_model="gpt-terra", context_max=None)
    assert helpers.enforce_agent_settings_consistency(s).context_max is None


def test_enforce_matching_window_returns_same_instance(helpers):
    s = AgentSettings(selected_model="gpt-terra", context_max=372_000)
    assert helpers.enforce_agent_settings_consistency(s) is s


def test_enforce_combines_effort_demotion_and_window_pin(helpers):
    s = AgentSettings(selected_model="gpt", effort="ultra", context_max=372_000)
    adjusted = helpers.enforce_agent_settings_consistency(s)
    assert adjusted.effort == "xhigh"
    assert adjusted.context_max == 272_000


# ─── enforce_synced_settings_consistency ────────────────────────────────────

def test_synced_context_reclamped_against_default_model(helpers, temp_settings):
    synced = {"codexDefaultModel": "gpt", "codexDefaultContextMax": 372_000}
    helpers.enforce_synced_settings_consistency(synced, dict(synced))
    assert synced["codexDefaultContextMax"] == 272_000


def test_synced_context_not_written_back_when_absent_from_changes(helpers, temp_settings):
    # Base contract: never mutate a key the client didn't send.
    synced = {"codexDefaultModel": "gpt", "codexDefaultContextMax": 372_000}
    helpers.enforce_synced_settings_consistency(synced, {"codexDefaultModel": "gpt"})
    assert synced["codexDefaultContextMax"] == 372_000


# ─── choices / constraints catalogue ────────────────────────────────────────

def test_context_max_choices_list_both_windows(helpers):
    assert helpers.get_agent_settings_choices()["context_max"] == [272_000, 372_000]


def test_constraints_map_each_window_to_its_models(helpers):
    constraints = helpers.get_agent_settings_constraints()["context_max"]
    assert set(constraints[272_000]) == {"gpt-5.5", "gpt", "gpt-5.4", "gpt-mini-5.4", "gpt-mini"}
    assert set(constraints[372_000]) == {
        "gpt-sol-5.6", "gpt-sol", "gpt-terra-5.6", "gpt-terra", "gpt-luna-5.6", "gpt-luna",
    }


# ─── extract_runtime_fields (task_started window recovery) ──────────────────

@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (258_400, 272_000),  # pre-5.6: 272K x 0.95
        (353_400, 372_000),  # GPT-5.6 tiers: 372K x 0.95
    ],
)
def test_runtime_window_recovered_from_task_started(published, expected):
    from twicc.providers.codex.compute import CodexSessionCompute

    line = {
        "type": "event_msg",
        "payload": {"type": "task_started", "model_context_window": published},
    }
    assert CodexSessionCompute().extract_runtime_fields(line)["context_max"] == expected


# ─── CLI alias resolution (--context-max) ───────────────────────────────────

class _FakeBootstrap:
    """Duck-typed ProviderBootstrap built from the live Codex catalogue."""

    def __init__(self, helpers):
        self.agent_settings_categories = {
            k.value if hasattr(k, "value") else k: v
            for k, v in helpers.AGENT_SETTINGS_CATEGORIES.items()
        }
        self.agent_settings_choices = helpers.get_agent_settings_choices()
        self.agent_settings_aliases = helpers.AGENT_SETTINGS_ALIASES
        self.model_registry = helpers.serialize_model_registry()
        self.untrusted_permission_modes = list(helpers.UNTRUSTED_PERMISSION_MODES)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("min", 272_000),
        ("max", 372_000),
        ("272k", 272_000),
        ("372k", 372_000),
    ],
)
def test_cli_context_max_alias_resolution(helpers, raw, expected):
    from twicc.cli._drop_request.aliases import resolve_overrides

    resolved, errors = resolve_overrides({"context_max": raw}, _FakeBootstrap(helpers))
    assert errors == []
    assert resolved["context_max"] == expected
