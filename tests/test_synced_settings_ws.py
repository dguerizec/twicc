"""WebSocket result frames for correlated synced-settings writes."""

from asgiref.sync import async_to_sync

from twicc.asgi import WSConsumer
from twicc.core.services import settings_mutation
from twicc.core.services.settings_mutation import SettingsDropError, SettingsUpdateResult


def _frames(monkeypatch, result, *, request_id="origin-write", value="HTTPS://PEER.EXAMPLE:443/"):
    async def fake_update(_patch, *, base_version):
        assert base_version == 4
        return result

    frames = []

    async def send_json(frame):
        frames.append(frame)

    monkeypatch.setattr(settings_mutation, "update_synced_settings", fake_update)
    consumer = WSConsumer()
    consumer.send_json = send_json
    payload = {
        "settings": {"peerBaseUrl": value},
        "baseVersion": 4,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    async_to_sync(consumer._handle_update_synced_settings)(payload)
    return frames


def test_correlated_acceptance_returns_the_authoritative_submitted_value(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {"peerBaseUrl": "https://peer.example"},
        {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result) == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_acceptance_without_correction_still_returns_one_result(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {}, {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result, value="https://peer.example") == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_rejection_sends_resync_then_the_matching_error_result(monkeypatch):
    error = SettingsDropError(
        "peerBaseUrl",
        "origin_conflict_ambiguous_authority",
        "The Peer and External addresses must be the same origin or use different authorities.",
    )
    clean = {"peerBaseUrl": "https://stored.example"}
    result = SettingsUpdateResult("rejected", 4, {}, clean, (error,))
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 4},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://stored.example"},
            "version": 4,
            "errors": [error._asdict()],
        },
    ]


def test_correlated_stale_rejection_has_an_empty_error_list(monkeypatch):
    clean = {"peerBaseUrl": "https://remote.example"}
    result = SettingsUpdateResult("rejected", 8, {}, clean)
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 8},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://remote.example"},
            "version": 8,
            "errors": [],
        },
    ]


def test_idless_rejection_keeps_the_legacy_error_frame(monkeypatch):
    error = SettingsDropError("peerBaseUrl", "invalid_origin_host", "Invalid address.")
    result = SettingsUpdateResult("rejected", 4, {}, {"peerBaseUrl": ""}, (error,))
    frames = _frames(monkeypatch, result, request_id=None)
    assert [frame["type"] for frame in frames] == ["synced_settings_updated", "error"]
    assert frames[1]["code"] == "invalid_synced_settings"
