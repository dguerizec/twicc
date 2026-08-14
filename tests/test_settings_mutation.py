"""Tests for the shared synced-settings merge service.

These run serverless: every test calls the service with ``broadcast=False`` so
no channel layer or orchestrator registry is touched. The ``temp_settings``
fixture points the settings file at a tmp path and clears the module cache.
"""

import pytest
from asgiref.sync import async_to_sync

import twicc.synced_settings as ss


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


def _update(patch, **kw):
    from twicc.core.services.settings_mutation import update_synced_settings

    # broadcast=False keeps the test off the channel layer / orchestrator.
    return async_to_sync(update_synced_settings)(patch, broadcast=False, **kw)


def test_scalar_patch_is_merged_and_version_bumped(temp_settings):
    r1 = _update({"autoUnpinOnArchive": False})
    assert r1.status == "accepted"
    assert ss.read_synced_settings()["autoUnpinOnArchive"] is False
    v1 = r1.version
    r2 = _update({"publicBaseUrl": "https://x"})
    assert r2.version == v1 + 1


class _NoAgentsRegistry:
    """Stub: every provider resolves to a manager with no live agents."""

    def get(self, provider):
        class _M:
            def get_active_agents(self_inner):
                return []

        return _M()


def test_default_provider_rebind_when_disabled(temp_settings, monkeypatch):
    # The merge guards a disable: it only sticks if the provider is RUNNING and
    # has no live agents. In a serverless test both must be stubbed, else the
    # disable is reverted by the transition guard and no rebind fires.
    import twicc.providers.state as pstate
    from twicc.core.services import settings_mutation as sm

    monkeypatch.setattr(pstate, "get_provider_state", lambda p: pstate.ProviderState.RUNNING)
    monkeypatch.setattr(sm, "get_agent_manager_registry", lambda: _NoAgentsRegistry())
    r = _update({"disabledProviders": ["claude_code"], "defaultProvider": "claude_code"})
    assert r.corrections.get("defaultProvider") == "codex"
    assert ss.read_synced_settings()["defaultProvider"] == "codex"


def test_base_version_stale_is_rejected(temp_settings):
    _update({"autoUnpinOnArchive": False})  # version -> 1
    r = _update({"autoUnpinOnArchive": True}, base_version=0)
    assert r.status == "rejected"
    assert ss.read_synced_settings()["autoUnpinOnArchive"] is False  # unchanged


def test_public_origin_patch_is_normalized_and_returned_as_correction(temp_settings):
    r = _update({"publicBaseUrl": " Public.Example.COM/", "shareBaseUrl": "localhost:3501"})
    assert r.status == "accepted"
    assert r.corrections == {
        "publicBaseUrl": "https://public.example.com",
        "shareBaseUrl": "http://localhost:3501",
    }
    settings = ss.read_synced_settings()
    assert settings["publicBaseUrl"] == "https://public.example.com"
    assert settings["shareBaseUrl"] == "http://localhost:3501"


def test_invalid_public_origin_rejects_the_whole_patch(temp_settings):
    before = ss.read_synced_settings()
    r = _update({"autoUnpinOnArchive": False, "peerBaseUrl": "ftp://peer.example.com"})
    assert r.status == "rejected"
    assert [(error.field, error.code) for error in r.errors] == [("peerBaseUrl", "invalid_origin_scheme")]
    after = ss.read_synced_settings()
    assert after["autoUnpinOnArchive"] == before["autoUnpinOnArchive"]
    assert after["peerBaseUrl"] == before["peerBaseUrl"]
    assert after["_version"] == before["_version"]


def test_unchanged_invalid_legacy_origin_does_not_block_full_snapshot(temp_settings):
    seeded = ss.read_synced_settings()
    seeded["publicBaseUrl"] = "ftp://legacy.example.com"
    ss.write_synced_settings(seeded)
    r = _update({"publicBaseUrl": "ftp://legacy.example.com", "terminalUseTmux": False})
    assert r.status == "accepted"
    assert ss.read_synced_settings()["publicBaseUrl"] == "ftp://legacy.example.com"
    assert ss.read_synced_settings()["terminalUseTmux"] is False


def test_update_from_payload_applies_patch(temp_settings):
    from twicc.core.services.settings_mutation import update_synced_settings_from_payload

    res = async_to_sync(update_synced_settings_from_payload)(
        {"kind": "settings:update", "patch": {"terminalUseTmux": False}, "broadcast": False},
    )
    assert res.success is True
    assert ss.read_synced_settings()["terminalUseTmux"] is False


def test_update_from_payload_returns_public_origin_validation_error(temp_settings):
    from twicc.core.services.settings_mutation import update_synced_settings_from_payload

    res = async_to_sync(update_synced_settings_from_payload)(
        {"kind": "settings:update", "patch": {"shareBaseUrl": "https://share.example.com/path"}, "broadcast": False},
    )
    assert res.success is False
    assert [(error.field, error.code) for error in res.errors] == [("shareBaseUrl", "invalid_origin_path")]


def test_notification_test_persists_tested(temp_settings, monkeypatch):
    from twicc.core.services import settings_mutation as sm

    # seed a target
    ss.write_synced_settings({
        **ss.read_synced_settings(),
        "externalNotificationTargets": [{"id": "t1", "url": "json://x", "tested": None}],
    })

    async def fake_test(urls):
        return [{"url_masked": "json://***", "ok": True, "error": None}]

    monkeypatch.setattr("twicc.external_notifications.test_notification_urls", fake_test)
    res = async_to_sync(sm.notification_test_from_payload)(
        {"kind": "settings:notification_test", "id": "t1", "broadcast": False},
    )
    assert res.success is True
    target = ss.read_synced_settings()["externalNotificationTargets"][0]
    assert target["tested"] is True


def test_origin_patch_allows_unchanged_invalid_stored_origin(temp_settings):
    seeded = ss.read_synced_settings()
    seeded["shareBaseUrl"] = "ftp://peer.example"
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()
    # The patch changes Peer only. The unchanged invalid Share value does not
    # block this repair and does not become a relationship operand (design §7).
    r = _update({"peerBaseUrl": "https://peer.example"})
    assert r.status == "accepted"
    after = ss.read_synced_settings()
    assert after["peerBaseUrl"] == "https://peer.example"
    assert after["shareBaseUrl"] == "ftp://peer.example"
    assert after["_version"] == before["_version"] + 1


def test_non_origin_patch_stays_allowed_with_invalid_stored_origin(temp_settings):
    seeded = ss.read_synced_settings()
    seeded["shareBaseUrl"] = "ftp://legacy.example.com"
    ss.write_synced_settings(seeded)
    r = _update({"terminalUseTmux": False, "shareBaseUrl": "ftp://legacy.example.com"})
    assert r.status == "accepted"
    assert ss.read_synced_settings()["terminalUseTmux"] is False
    assert ss.read_synced_settings()["shareBaseUrl"] == "ftp://legacy.example.com"


@pytest.mark.parametrize(
    "stored, submitted, expected_status",
    [
        (0.0, 0, "accepted"),
        (1.0, 1, "accepted"),
        (0, False, "rejected"),
        (False, 0, "rejected"),
    ],
)
def test_json_scalar_number_round_trip_and_boolean_distinction(
    temp_settings, stored, submitted, expected_status,
):
    seeded = ss.read_synced_settings()
    seeded["peerBaseUrl"] = stored
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()

    result = _update({"terminalUseTmux": False, "peerBaseUrl": submitted})

    assert result.status == expected_status
    after = ss.read_synced_settings()
    if expected_status == "accepted":
        assert after["terminalUseTmux"] is False
        assert after["peerBaseUrl"] == submitted
        assert after["_version"] == before["_version"] + 1
    else:
        assert [(error.field, error.code) for error in result.errors] == [
            ("peerBaseUrl", "invalid_origin_type"),
        ]
        assert after == before


@pytest.mark.parametrize(
    "stored, submitted, expected_status",
    [
        ([0.0], [0], "accepted"),
        ({"value": 1.0}, {"value": 1}, "accepted"),
        ({"value": 0}, {"value": False}, "rejected"),
        ([0], [False], "rejected"),
    ],
)
def test_json_nested_number_round_trip_and_boolean_distinction(
    temp_settings, stored, submitted, expected_status,
):
    seeded = ss.read_synced_settings()
    seeded["peerBaseUrl"] = stored
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()

    result = _update({"terminalUseTmux": False, "peerBaseUrl": submitted})

    assert result.status == expected_status
    after = ss.read_synced_settings()
    if expected_status == "accepted":
        assert after["terminalUseTmux"] is False
        assert after["peerBaseUrl"] == submitted
        assert after["_version"] == before["_version"] + 1
    else:
        assert [(error.field, error.code) for error in result.errors] == [
            ("peerBaseUrl", "invalid_origin_type"),
        ]
        assert after == before


def test_origin_patch_rejects_relationship_conflicts_atomically(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    before = ss.read_synced_settings()
    r = _update({"shareBaseUrl": "https://x.example:9443"})
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
    assert r.errors[0].message == "The Share host must use a different hostname from the External address."
    after = ss.read_synced_settings()
    assert after.get("shareBaseUrl", "") == before.get("shareBaseUrl", "")
    assert after["_version"] == before["_version"]


def test_origin_patch_rejects_ambiguous_peer_external(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    r = _update({"peerBaseUrl": "http://x.example"})
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("peerBaseUrl", "origin_conflict_ambiguous_authority"),
        ("publicBaseUrl", "origin_conflict_ambiguous_authority"),
    ]
    assert r.errors[0].message == (
        "The Peer and External addresses must be the same origin or use different authorities."
    )


def test_origin_patch_accepts_shared_peer_and_external(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    r = _update({"peerBaseUrl": "https://x.example"})
    assert r.status == "accepted"
    assert ss.read_synced_settings()["peerBaseUrl"] == "https://x.example"


@pytest.mark.parametrize(
    "first_field,first_value,second_field,second_value,expected_public,expected_share",
    [
        (
            "publicBaseUrl", "https://share.example",
            "shareBaseUrl", "https://final-share.example",
            "https://share.example", "https://final-share.example",
        ),
        (
            "shareBaseUrl", "https://app.example",
            "publicBaseUrl", "https://final-app.example",
            "https://final-app.example", "https://app.example",
        ),
    ],
)
def test_two_invalid_origins_can_be_repaired_in_either_order(
    temp_settings, first_field, first_value, second_field, second_value, expected_public, expected_share,
):
    seeded = ss.read_synced_settings()
    seeded["publicBaseUrl"] = "ftp://app.example"
    seeded["shareBaseUrl"] = "ftp://share.example"
    ss.write_synced_settings(seeded)
    # Each repair order keeps the other invalid field unchanged.
    first = _update({first_field: first_value})
    assert first.status == "accepted"
    untouched = "shareBaseUrl" if first_field == "publicBaseUrl" else "publicBaseUrl"
    assert ss.read_synced_settings()[untouched].startswith("ftp://")
    second = _update({second_field: second_value})
    assert second.status == "accepted"
    settings = ss.read_synced_settings()
    assert settings["publicBaseUrl"] == expected_public
    assert settings["shareBaseUrl"] == expected_share


def test_multi_field_origin_patch_reports_all_errors_and_writes_nothing(temp_settings):
    before = ss.read_synced_settings()
    r = _update({
        "publicBaseUrl": "ftp://app.example",
        "shareBaseUrl": "https://x.example",
        "peerBaseUrl": "http://x.example:8443",
    })
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
    assert r.errors[1].message == "The Share host must use a different hostname from the Peer address."
    assert ss.read_synced_settings() == before


def test_update_from_payload_returns_origin_relationship_errors(temp_settings):
    from asgiref.sync import async_to_sync
    from twicc.core.services.settings_mutation import update_synced_settings_from_payload

    _update({"publicBaseUrl": "https://x.example"})
    res = async_to_sync(update_synced_settings_from_payload)({
        "kind": "settings:update",
        "patch": {"shareBaseUrl": "https://x.example:9443"},
        "broadcast": False,
    })
    assert res.success is False
    assert [(error.field, error.code) for error in res.errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
