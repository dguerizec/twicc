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
