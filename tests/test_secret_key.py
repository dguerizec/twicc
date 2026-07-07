"""Per-install SECRET_KEY: load-or-create, permissions, env override."""

import pytest

from twicc.secret_key import load_or_create_secret_key


@pytest.fixture(autouse=True)
def _isolated_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "twicc.paths.get_secret_key_path", lambda: tmp_path / "secret-key",
    )
    monkeypatch.delenv("TWICC_SECRET_KEY", raising=False)


def test_creates_persists_and_reloads(tmp_path):
    key = load_or_create_secret_key()
    path = tmp_path / "secret-key"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert path.read_text().strip() == key
    assert len(key) >= 50
    assert load_or_create_secret_key() == key


def test_existing_file_wins(tmp_path):
    (tmp_path / "secret-key").write_text("already-there\n")
    assert load_or_create_secret_key() == "already-there"


def test_env_override_skips_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TWICC_SECRET_KEY", "from-env")
    assert load_or_create_secret_key() == "from-env"
    assert not (tmp_path / "secret-key").exists()


def test_empty_file_regenerates(tmp_path):
    path = tmp_path / "secret-key"
    path.write_text("\n")
    key = load_or_create_secret_key()
    assert key
    assert path.read_text().strip() == key
