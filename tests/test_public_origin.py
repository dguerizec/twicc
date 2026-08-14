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


def test_canonicalize_hostname_strict_contract():
    from twicc.core.services.public_origin import canonicalize_hostname

    # localhost is case-insensitive with a fixed canonical value.
    assert canonicalize_hostname("LocalHost", bracketed=False) == ("localhost", False)
    # Canonical IPv4 only.
    assert canonicalize_hostname("192.168.1.42", bracketed=False) == ("192.168.1.42", False)
    assert canonicalize_hostname("192.168.001.1", bracketed=False).hostname is None
    assert canonicalize_hostname("1.2.3", bracketed=False).hostname is None
    # Bracketed IPv6 canonicalizes to lower-case compressed RFC 5952.
    assert canonicalize_hostname("0:0:0:0:0:0:0:1", bracketed=True) == ("::1", True)
    assert canonicalize_hostname("2001:DB8::1", bracketed=True) == ("2001:db8::1", True)
    assert canonicalize_hostname("::ffff:1.2.3.4", bracketed=True) == ("::ffff:1.2.3.4", True)
    assert canonicalize_hostname("1.2.3.4", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%eth0", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%25eth0", bracketed=True).hostname is None
    # DNS grammar: LDH labels, alphanumeric edges, 1-63 chars per label.
    assert canonicalize_hostname("Example.COM", bracketed=False) == ("example.com", False)
    assert canonicalize_hostname("devbox", bracketed=False) == ("devbox", False)
    assert canonicalize_hostname("a..example", bracketed=False).hostname is None
    assert canonicalize_hostname("example.com.", bracketed=False).hostname is None
    assert canonicalize_hostname("-a.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a-.example", bracketed=False).hostname is None
    assert canonicalize_hostname("my_host.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a" * 63 + ".example", bracketed=False).hostname == "a" * 63 + ".example"
    assert canonicalize_hostname("a" * 64 + ".example", bracketed=False).hostname is None
    long_253 = ".".join(["a" * 63] * 3 + ["a" * 61])
    assert canonicalize_hostname(long_253, bracketed=False).hostname == long_253
    assert canonicalize_hostname(long_253 + "a", bracketed=False).hostname is None
    # ASCII only: Unicode and percent escapes are invalid, never converted.
    assert canonicalize_hostname("exämple.com", bracketed=False).hostname is None
    assert canonicalize_hostname("%65xample.com", bracketed=False).hostname is None
    # A-labels: valid IDNA2008 round-trips survive, malformed ones are invalid.
    assert canonicalize_hostname("XN--FA-HIA.de", bracketed=False) == ("xn--fa-hia.de", False)
    assert canonicalize_hostname("xn--a-ecp.example", bracketed=False).hostname is None
    assert canonicalize_hostname("xn--e28h.example", bracketed=False).hostname is None


def test_normalize_public_origin_strict_hostnames():
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").value == "https://[::1]:8443"
    assert normalize_public_origin("HTTPS://XN--FA-HIA.DE").value == "https://xn--fa-hia.de"
    assert normalize_public_origin("https://xn--.example").error == "host"
    assert normalize_public_origin("https://exämple.com").error == "host"
    assert normalize_public_origin("https://%65xample.com").error == "host"
    assert normalize_public_origin("https://[1.2.3.4]").error == "host"
    assert normalize_public_origin("https://example.com:").error == "port"
    assert normalize_public_origin("https://exa\tmple.com").error == "host"
    assert normalize_public_origin("https://example.com:8\t0").error == "port"
    assert normalize_public_origin("https://example.com?").error == "query"
    assert normalize_public_origin("https://example.com#").error == "fragment"
    assert normalize_public_origin("https://example.com?\t#").error == "query"
    assert normalize_public_origin("https://example.com#x?y").error == "fragment"


def test_normalized_origin_exposes_routing_authority():
    assert normalize_public_origin("https://Example.com:443").authority == "example.com"
    assert normalize_public_origin("https://Example.com:8443").authority == "example.com:8443"
    assert normalize_public_origin("http://example.com").authority == "example.com"
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").authority == "[::1]:8443"
    assert normalize_public_origin("").authority is None
    assert normalize_public_origin("ftp://x.example").authority is None


def test_classify_peer_external():
    from twicc.core.services.public_origin import classify_peer_external

    def parse(value):
        return normalize_public_origin(value)

    assert classify_peer_external(parse("https://x.example"), parse("https://x.example")) == "shared"
    assert classify_peer_external(parse("http://x.example"), parse("https://x.example")) == "ambiguous"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example")) == "dedicated"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example:7443")) == "dedicated"
    assert classify_peer_external(parse("https://x.example"), parse("")) == "dedicated"
    assert classify_peer_external(parse(""), parse("https://x.example")) is None
    assert classify_peer_external(parse("ftp://x.example"), parse("https://x.example")) is None
    # IPv6 spellings normalize before comparison: expanded peer == compressed external.
    assert classify_peer_external(parse("https://[0:0:0:0:0:0:0:1]:8443"), parse("https://[::1]:8443")) == "shared"


def test_validate_origin_settings():
    from twicc.core.services.public_origin import validate_origin_settings

    all_fields = {"publicBaseUrl", "shareBaseUrl", "peerBaseUrl"}
    assert validate_origin_settings("", "", "", changed_fields=all_fields) == ()
    assert validate_origin_settings(
        "https://app.example", "https://share.example", "https://peer.example", changed_fields=all_fields,
    ) == ()
    # Structural errors name invalid changed fields only.
    errors = validate_origin_settings(
        "ftp://app.example", "https://share.example", 42, changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("peerBaseUrl", "invalid_origin_type"),
    ]
    # An unchanged invalid field does not block another field's repair.
    assert validate_origin_settings(
        "ftp://app.example", "https://share.example", "https://peer.example",
        changed_fields={"peerBaseUrl"},
    ) == ()
    # Share/External hostname conflict names both fields; a port does not help.
    errors = validate_origin_settings(
        "https://x.example", "https://x.example:9443", "", changed_fields={"shareBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
    # Share/Peer hostname conflict.
    errors = validate_origin_settings(
        "", "https://x.example", "http://x.example:8443", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
    # Ambiguous Peer/External: same authority, different origins.
    errors = validate_origin_settings(
        "https://x.example", "", "http://x.example", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("peerBaseUrl", "origin_conflict_ambiguous_authority"),
        ("publicBaseUrl", "origin_conflict_ambiguous_authority"),
    ]
    # Shared Peer/External is valid.
    assert validate_origin_settings(
        "https://x.example", "", "https://x.example", changed_fields={"peerBaseUrl"},
    ) == ()
    # A structural error does not hide a conflict between other changed fields.
    errors = validate_origin_settings(
        "ftp://app.example", "https://x.example", "http://x.example:8443",
        changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]


def test_authority_cases_match_backend_contract():
    for case in CASES["authority_cases"]:
        result = normalize_public_origin(case["input"])
        assert (result.hostname, result.authority) == (case["hostname"], case["authority"]), case["name"]


def test_cross_cases_match_backend_contract():
    from twicc.core.services.public_origin import classify_peer_external, validate_origin_settings

    for case in CASES["cross_cases"]:
        errors = validate_origin_settings(
            case["publicBaseUrl"], case["shareBaseUrl"], case["peerBaseUrl"],
            changed_fields=set(case["changed_fields"]),
        )
        assert [{"field": error.field, "code": error.code} for error in errors] == case["errors"], case["name"]
        if case["peer_routing"] is not None:
            routing = classify_peer_external(
                normalize_public_origin(case["peerBaseUrl"]),
                normalize_public_origin(case["publicBaseUrl"]),
            )
            assert routing == case["peer_routing"], case["name"]


def test_every_frontend_rejection_is_also_a_backend_rejection():
    backend = {case["input"]: case for case in CASES["cases"]}
    for case in CASES["frontend_input_cases"]:
        if case["error"] is None:
            continue
        assert case["input"] in backend, case["name"]
        assert backend[case["input"]]["error"] is not None, case["name"]
