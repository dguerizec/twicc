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
