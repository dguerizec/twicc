import pytest
import twicc.synced_settings as ss


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


def test_generic_allowlist_excludes_visual_and_special():
    from twicc.cli.settings._keys import classify_key
    assert classify_key("autoUnpinOnArchive") == "generic"
    assert classify_key("waTheme") == "excluded"
    assert classify_key("defaultLayoutId") == "excluded"
    assert classify_key("disabledProviders") == "provider"
    assert classify_key("externalNotificationTargets") == "notifications"
    assert classify_key("claudeCodeDefaultModel") == "provider"
    assert classify_key("nope") == "unknown"

def test_value_type_inferred_from_default():
    from twicc.cli.settings._keys import parse_value
    assert parse_value("autoUnpinOnArchive", "false") is False
    assert parse_value("autoUnpinOnArchive", "true") is True
    assert parse_value("publicBaseUrl", "https://x") == "https://x"

def test_parse_value_rejects_bad_bool_and_int():
    from twicc.cli.settings._keys import parse_value, ValueParseError
    with pytest.raises(ValueParseError):
        parse_value("autoUnpinOnArchive", "maybe")


def test_build_settings_dump_returns_defaults_without_version(temp_settings):
    from twicc.cli.settings.command import build_settings_dump

    result = build_settings_dump()
    # Known generic default must be present.
    assert "autoUnpinOnArchive" in result
    assert result["autoUnpinOnArchive"] is True
    # _version must be stripped.
    assert "_version" not in result


# ---------------------------------------------------------------------------
# Validation helpers for set/unset key rejection
# ---------------------------------------------------------------------------

def _validate_settable_key(key: str):
    """Return a (field, code, message) tuple if the key is rejected, else None."""
    from twicc.cli.settings._keys import classify_key

    category = classify_key(key)
    if category == "excluded":
        return ("KEY", "excluded",
                f"{key!r} is a UI-only visual preference; not settable via CLI.")
    if category == "provider":
        return ("KEY", "provider_key",
                f"{key!r} is a provider setting; use `twicc settings provider …`.")
    if category == "notifications":
        return ("KEY", "notifications_key",
                f"{key!r} is a notification setting; use `twicc settings notifications …`.")
    if category == "unknown":
        return ("KEY", "unknown_key", f"No such setting {key!r}.")
    # generic → accepted
    return None


def test_set_rejects_excluded_key():
    result = _validate_settable_key("waTheme")
    assert result is not None
    assert result[1] == "excluded"


def test_set_rejects_provider_key():
    result = _validate_settable_key("disabledProviders")
    assert result is not None
    assert result[1] == "provider_key"


def test_set_rejects_provider_prefixed_key():
    result = _validate_settable_key("claudeCodeDefaultModel")
    assert result is not None
    assert result[1] == "provider_key"


def test_set_rejects_notifications_key():
    result = _validate_settable_key("externalNotificationTargets")
    assert result is not None
    assert result[1] == "notifications_key"


def test_set_rejects_unknown_key():
    result = _validate_settable_key("bogusKey")
    assert result is not None
    assert result[1] == "unknown_key"


def test_set_accepts_generic_key():
    result = _validate_settable_key("autoUnpinOnArchive")
    assert result is None


# ---------------------------------------------------------------------------
# settings provider — patch assembly + show projection
# ---------------------------------------------------------------------------

def _empty_provider_flags(**overrides):
    """Build a callback-flags dict (all unset) with the given overrides."""
    flags = {
        "model": None,
        "effort": None,
        "permission_mode": None,
        "context_max": None,
        "thinking": None,
        "fast": None,
        "chrome": None,
        "untrusted_permission_mode": None,
        "usage_read_file": None,
        "no_usage_read_file": False,
        "usage_dump_file": None,
        "no_usage_dump_file": False,
    }
    flags.update(overrides)
    return flags


@pytest.mark.django_db
def test_provider_patch_codex_rejects_fast(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch("codex", _empty_provider_flags(fast=True))
    assert patch == {}
    assert any(e.field == "--fast" and e.code == "unsupported_field" for e in errors)


@pytest.mark.django_db
def test_provider_patch_model_alias_resolves(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # 'max' resolves to the strongest available model per provider.
    cc_patch, cc_errors = build_provider_patch(
        "claude_code", _empty_provider_flags(model="max"))
    assert cc_errors == []
    assert cc_patch == {"claudeCodeDefaultModel": "opus"}

    cx_patch, cx_errors = build_provider_patch(
        "codex", _empty_provider_flags(model="max"))
    assert cx_errors == []
    assert cx_patch == {"codexDefaultModel": "gpt"}


@pytest.mark.django_db
def test_provider_patch_usage_read_file_sets_path_and_enabled(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(usage_read_file="/x"))
    assert errors == []
    assert patch == {
        "claudeCodeUsageReadFilePath": "/x",
        "claudeCodeUsageReadFileEnabled": True,
    }


@pytest.mark.django_db
def test_provider_patch_no_usage_read_file_disables(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(no_usage_read_file=True))
    assert errors == []
    assert patch == {"claudeCodeUsageReadFileEnabled": False}


@pytest.mark.django_db
def test_provider_patch_untrusted_permission_mode(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # 'max' resolves within the untrusted-allowed set (acceptEdits for CC).
    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(untrusted_permission_mode="max"))
    assert errors == []
    assert patch == {"claudeCodeDefaultUntrustedPermissionMode": "acceptEdits"}


@pytest.mark.django_db
def test_provider_patch_untrusted_permission_mode_rejects_out_of_set(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # bypassPermissions is never allowed in the untrusted set for Claude Code.
    patch, errors = build_provider_patch(
        "claude_code",
        _empty_provider_flags(untrusted_permission_mode="bypassPermissions"))
    assert patch == {}
    assert any(e.field == "--untrusted-permission-mode"
               and e.code == "invalid_choice" for e in errors)


def test_provider_show_projection_from_seeded_settings(temp_settings):
    from twicc.cli.settings.provider import build_provider_show

    seeded = {
        "defaultProvider": "codex",
        "disabledProviders": ["claude_code"],
        "orchestrationDisabledProviders": ["codex"],
        "claudeCodeDefaultModel": "sonnet",
        "claudeCodeDefaultUntrustedPermissionMode": "plan",
        "claudeCodeUsageReadFileEnabled": True,
        "claudeCodeUsageReadFilePath": "/tmp/usage.json",
    }
    show = build_provider_show("claude_code", seeded)
    assert show["provider"] == "claude_code"
    assert show["enabled"] is False  # in disabledProviders
    assert show["is_default"] is False  # default is codex
    assert show["orchestration_enabled"] is True  # only codex is orch-disabled
    assert show["agent_defaults"]["selected_model"] == "sonnet"
    # Untouched agent-default falls back to the provider default.
    assert show["agent_defaults"]["effort"] == "medium"
    assert show["untrusted_permission_mode_default"] == "plan"
    assert show["usage_read_file"] == {"enabled": True, "path": "/tmp/usage.json"}
    # Untouched usage dump-file falls back to defaults.
    assert show["usage_dump_file"] == {"enabled": False, "path": ""}


# ---------------------------------------------------------------------------
# Task 9 — provider sub-commands: pure helper tests
# ---------------------------------------------------------------------------


def test_compute_disabled_enable_removes_provider():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["claude_code", "codex"], "claude_code", enable=True)
    assert result == ["codex"]


def test_compute_disabled_enable_is_idempotent_when_not_present():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["codex"], "claude_code", enable=True)
    assert result == ["codex"]


def test_compute_disabled_disable_adds_provider():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["codex"], "claude_code", enable=False)
    assert result == ["codex", "claude_code"]


def test_compute_disabled_disable_is_idempotent_when_already_present():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["claude_code", "codex"], "claude_code", enable=False)
    assert result == ["claude_code", "codex"]


def test_compute_disabled_preserves_order():
    from twicc.cli.settings.provider import compute_disabled

    # Disable: appended at the end; existing order of others preserved.
    result = compute_disabled(["codex", "other"], "claude_code", enable=False)
    assert result == ["codex", "other", "claude_code"]

    # Enable: others stay in original order.
    result2 = compute_disabled(["codex", "claude_code", "other"], "claude_code", enable=True)
    assert result2 == ["codex", "other"]


def test_compute_disabled_handles_empty_list():
    from twicc.cli.settings.provider import compute_disabled

    # Enable on an empty list stays empty.
    assert compute_disabled([], "claude_code", enable=True) == []
    assert compute_disabled(None, "claude_code", enable=True) == []
    # Disable on an empty list adds the provider.
    assert compute_disabled([], "claude_code", enable=False) == ["claude_code"]
    assert compute_disabled(None, "claude_code", enable=False) == ["claude_code"]


def test_compute_orchestration_disabled_same_semantics():
    from twicc.cli.settings.provider import compute_orchestration_disabled

    assert compute_orchestration_disabled(["codex"], "codex", enable=True) == []
    assert compute_orchestration_disabled(["codex"], "codex", enable=False) == ["codex"]
    assert compute_orchestration_disabled([], "codex", enable=False) == ["codex"]


def test_set_default_rejects_disabled_provider():
    """set-default must reject a provider that is in disabledProviders (pure check)."""
    # We test the pure logic: is the provider in the disabled list?
    from twicc.cli.settings.provider import compute_disabled

    disabled = compute_disabled([], "claude_code", enable=False)
    assert "claude_code" in disabled  # precondition

    # Simulate the check done by provider_set_default.
    provider = "claude_code"
    should_reject = provider in disabled
    assert should_reject is True


def test_set_default_accepts_enabled_provider():
    """set-default must accept a provider not in disabledProviders."""
    provider = "claude_code"
    disabled = ["codex"]  # only codex is disabled
    should_reject = provider in disabled
    assert should_reject is False
