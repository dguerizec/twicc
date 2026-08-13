import orjson
import pytest

from twicc.core.services.public_origin import normalize_public_origin, repair_legacy_public_origin, usable_public_origin


CASES = orjson.loads((__import__("pathlib").Path(__file__).parent / "fixtures/public_origin_cases.json").read_bytes())


def test_normalize_public_origin_matches_shared_contract():
    for case in CASES["cases"]:
        result = normalize_public_origin(case["input"])
        assert (result.value, result.error) == (case["value"], case["error"]), case["name"]


def test_repair_legacy_public_origin_matches_shared_contract():
    for case in CASES["repair_cases"]:
        result = repair_legacy_public_origin(case["input"])
        assert (result.value, result.error) == (case["value"], case["error"]), case["name"]


def test_normalized_origin_exposes_metadata():
    result = normalize_public_origin("HTTPS://Example.COM:8443/")
    assert result.scheme == "https"
    assert result.hostname == "example.com"
    assert result.port == 8443


def test_usable_public_origin_fails_closed_for_invalid_value():
    assert usable_public_origin("https://valid.example.com/") == "https://valid.example.com"
    assert usable_public_origin("ftp://unsafe.example.com") == ""


def test_settings_read_only_migrates_deployed_public_origins(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({
        "publicBaseUrl": "public.example.com",
        "shareBaseUrl": "HTTPS://Share.Example.COM/",
        "peerBaseUrl": "peer.example.com",
    }))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        settings = ss.read_synced_settings()
        assert settings["publicBaseUrl"] == "https://public.example.com"
        assert settings["shareBaseUrl"] == "https://share.example.com"
        assert settings["peerBaseUrl"] == "peer.example.com"
        persisted = orjson.loads(path.read_bytes())
        assert persisted["publicBaseUrl"] == "https://public.example.com"
        assert persisted["shareBaseUrl"] == "https://share.example.com"
        assert persisted["peerBaseUrl"] == "peer.example.com"
    finally:
        ss._cache.clear()


def test_public_origin_migration_is_idempotent():
    import twicc.synced_settings as ss

    settings = {
        "publicBaseUrl": "public.example.com",
        "shareBaseUrl": "https://share.example.com/base",
    }
    assert ss._migrate_legacy_settings(settings) is True
    migrated = settings.copy()
    assert ss._migrate_legacy_settings(settings) is False
    assert settings == migrated


@pytest.mark.parametrize("key", ["publicBaseUrl", "shareBaseUrl"])
def test_settings_read_retains_unsafe_public_origin(key, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({key: "ftp://unsafe.example.com"}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()[key] == "ftp://unsafe.example.com"
        assert orjson.loads(path.read_bytes())[key] == "ftp://unsafe.example.com"
    finally:
        ss._cache.clear()


def test_settings_read_retains_non_string_public_origin(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({"shareBaseUrl": 42}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()["shareBaseUrl"] == 42
        assert orjson.loads(path.read_bytes())["shareBaseUrl"] == 42
    finally:
        ss._cache.clear()
