"""Saved Browser-pane URLs: entry validation, list ops, and atomic updates.

Covers the shared shape ``[{url, label?, default?}]`` used by
``Project.browser_urls`` and a workspace's ``browserUrls``:

- the pure helpers in :mod:`twicc.workspaces` (validation/canonicalization,
  add/remove/set-default ops, legacy single-URL migration, op-payload cleaner);
- the project side (:func:`twicc.projects.update_project_atomic` ops and the
  ``project:update`` payload glue);
- the workspace side (atomic ops on ``workspaces.json``, including the
  on-read migration of the legacy ``browserUrl`` key).
"""

from __future__ import annotations

import asyncio

import orjson
import pytest

from twicc import paths
from twicc.workspaces import (
    add_browser_url_entry,
    clean_browser_url_ops,
    migrate_workspace_browser_urls,
    normalize_browser_url_entries,
    read_workspaces,
    remove_browser_url_entry,
    set_default_browser_url_entry,
)


# ---------------------------------------------------------------------------
# normalize_browser_url_entries
# ---------------------------------------------------------------------------


def test_normalize_entries_canonicalizes_sparse():
    entries, errors = normalize_browser_url_entries([
        {"url": "  http://localhost:3000  ", "label": "  Front  ", "default": True},
        {"url": "https://example.com", "label": "", "default": False},
    ])
    assert errors == []
    assert entries == [
        {"url": "http://localhost:3000", "label": "Front", "default": True},
        {"url": "https://example.com"},
    ]


def test_normalize_entries_accepts_empty_list():
    entries, errors = normalize_browser_url_entries([])
    assert (entries, errors) == ([], [])


@pytest.mark.parametrize("value", ["nope", {"url": "http://x"}, None])
def test_normalize_entries_rejects_non_list(value):
    entries, errors = normalize_browser_url_entries(value)
    assert entries is None
    assert errors[0].code == "invalid_value"


@pytest.mark.parametrize("entry, code", [
    ("http://x.test", "invalid_value"),                       # entry not a dict
    ({"label": "x"}, "invalid_value"),                        # missing url
    ({"url": "   "}, "invalid_value"),                        # empty url
    ({"url": "ftp://x.test"}, "invalid_value"),               # non-http(s)
    ({"url": "http://x.test", "label": 3}, "invalid_value"),  # label not a string
    ({"url": "http://x.test", "label": "x" * 101}, "invalid_value"),
    ({"url": "http://x.test", "default": "yes"}, "invalid_value"),
])
def test_normalize_entries_rejects_bad_entry(entry, code):
    entries, errors = normalize_browser_url_entries([entry])
    assert entries is None
    assert errors[0].code == code


def test_normalize_entries_rejects_duplicate_url():
    entries, errors = normalize_browser_url_entries([
        {"url": "http://x.test"},
        {"url": " http://x.test "},
    ])
    assert entries is None
    assert errors[0].code == "duplicate_url"


def test_normalize_entries_rejects_multiple_defaults():
    entries, errors = normalize_browser_url_entries([
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test", "default": True},
    ])
    assert entries is None
    assert errors[0].code == "multiple_defaults"


# ---------------------------------------------------------------------------
# List ops
# ---------------------------------------------------------------------------


def test_add_first_url_becomes_default():
    assert add_browser_url_entry([], "http://a.test") == [
        {"url": "http://a.test", "default": True},
    ]


def test_add_second_url_keeps_existing_default():
    entries = [{"url": "http://a.test", "default": True}]
    assert add_browser_url_entry(entries, "http://b.test", label="B") == [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test", "label": "B"},
    ]


def test_add_with_set_default_moves_the_flag():
    entries = [{"url": "http://a.test", "default": True}]
    assert add_browser_url_entry(entries, "http://b.test", set_default=True) == [
        {"url": "http://a.test"},
        {"url": "http://b.test", "default": True},
    ]


def test_add_existing_url_is_idempotent_and_updates_label():
    entries = [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test", "label": "old"},
    ]
    updated = add_browser_url_entry(entries, "http://b.test", label="new", set_default=True)
    assert updated == [
        {"url": "http://a.test"},
        {"url": "http://b.test", "label": "new", "default": True},
    ]
    # No label given → the existing label is preserved.
    assert add_browser_url_entry(entries, "http://b.test") == entries


def test_remove_url_is_idempotent_and_keeps_no_default():
    entries = [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test"},
    ]
    # Removing the default entry does NOT promote another one.
    assert remove_browser_url_entry(entries, "http://a.test") == [{"url": "http://b.test"}]
    assert remove_browser_url_entry(entries, "http://absent.test") == entries


def test_set_default_moves_flag_or_reports_missing():
    entries = [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test"},
    ]
    updated, found = set_default_browser_url_entry(entries, "http://b.test")
    assert found is True
    assert updated == [{"url": "http://a.test"}, {"url": "http://b.test", "default": True}]

    updated, found = set_default_browser_url_entry(entries, "http://absent.test")
    assert found is False
    assert updated == entries


def test_ops_do_not_mutate_their_input():
    entries = [{"url": "http://a.test", "default": True}]
    add_browser_url_entry(entries, "http://b.test", set_default=True)
    remove_browser_url_entry(entries, "http://a.test")
    set_default_browser_url_entry(entries, "http://a.test")
    assert entries == [{"url": "http://a.test", "default": True}]


# ---------------------------------------------------------------------------
# Legacy migration (workspace dicts)
# ---------------------------------------------------------------------------


def test_migrate_legacy_browser_url():
    ws = {"id": "w", "browserUrl": " http://a.test "}
    assert migrate_workspace_browser_urls(ws) is True
    assert ws == {"id": "w", "browserUrls": [{"url": "http://a.test", "default": True}]}


def test_migrate_legacy_empty_value():
    ws = {"id": "w", "browserUrl": None}
    assert migrate_workspace_browser_urls(ws) is True
    assert ws == {"id": "w", "browserUrls": []}


def test_migrate_keeps_new_key_when_both_present():
    ws = {"id": "w", "browserUrl": "http://old.test", "browserUrls": [{"url": "http://new.test"}]}
    assert migrate_workspace_browser_urls(ws) is True
    assert ws == {"id": "w", "browserUrls": [{"url": "http://new.test"}]}


def test_migrate_noop_without_legacy_key():
    ws = {"id": "w", "browserUrls": []}
    assert migrate_workspace_browser_urls(ws) is False


# ---------------------------------------------------------------------------
# clean_browser_url_ops (payload glue shared by project/workspace updates)
# ---------------------------------------------------------------------------


def test_clean_ops_full_payload():
    ops, errors = clean_browser_url_ops({
        "add_browser_url": {"url": " http://a.test ", "label": " A ", "set_default": True},
        "remove_browser_url": "http://b.test",
        "set_default_browser_url": "http://c.test",
        "clear_browser_urls": True,
    })
    assert errors == []
    assert ops == {
        "add_browser_url": {"url": "http://a.test", "label": "A", "set_default": True},
        "remove_browser_url": "http://b.test",
        "set_default_browser_url": "http://c.test",
        "clear_browser_urls": True,
    }


def test_clean_ops_defaults_to_noop():
    ops, errors = clean_browser_url_ops({})
    assert errors == []
    assert ops == {
        "add_browser_url": None,
        "remove_browser_url": None,
        "set_default_browser_url": None,
        "clear_browser_urls": False,
    }


@pytest.mark.parametrize("payload", [
    {"add_browser_url": "http://a.test"},                      # not a dict
    {"add_browser_url": {"url": "ftp://a.test"}},              # bad scheme
    {"add_browser_url": {"url": "http://a.test", "label": "x" * 101}},
    {"add_browser_url": {"url": "http://a.test", "set_default": "yes"}},
    {"remove_browser_url": 3},
    {"set_default_browser_url": "   "},
    {"clear_browser_urls": "yes"},
])
def test_clean_ops_rejects_bad_payloads(payload):
    _ops, errors = clean_browser_url_ops(payload)
    assert errors


# ---------------------------------------------------------------------------
# Project atomic ops
# ---------------------------------------------------------------------------


@pytest.fixture
def project(db):
    from twicc.core.models import Project

    return Project.objects.create(id="-tmp-burls", directory="/tmp/burls", name="burls")


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer only starts at app boot; run write factories
    transparently (``update_project_atomic`` imports the symbol lazily from
    ``twicc.providers.db_writer``, so the patch lands on that module)."""
    async def _passthrough(coro_factory):
        return await coro_factory()

    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)


@pytest.mark.django_db(transaction=True)
def test_project_add_remove_set_default_clear(project):
    from twicc.core.models import Project
    from twicc.projects import update_project_atomic

    def run(**kwargs):
        return asyncio.run(update_project_atomic(project.id, **kwargs))

    result = run(add_browser_url={"url": "http://a.test", "label": None, "set_default": False})
    assert result.success
    result = run(add_browser_url={"url": "http://b.test", "label": "B", "set_default": True})
    assert result.success
    assert Project.objects.get(id=project.id).browser_urls == [
        {"url": "http://a.test"},
        {"url": "http://b.test", "label": "B", "default": True},
    ]

    result = run(set_default_browser_url="http://a.test")
    assert result.success
    assert Project.objects.get(id=project.id).browser_urls == [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test", "label": "B"},
    ]

    result = run(set_default_browser_url="http://absent.test")
    assert not result.success
    assert result.errors[0].code == "url_not_found"

    result = run(remove_browser_url="http://a.test")
    assert result.success
    assert Project.objects.get(id=project.id).browser_urls == [
        {"url": "http://b.test", "label": "B"},
    ]

    result = run(clear_browser_urls=True)
    assert result.success
    assert Project.objects.get(id=project.id).browser_urls == []


@pytest.mark.django_db(transaction=True)
def test_project_update_payload_glue(project):
    from twicc.core.models import Project
    from twicc.core.services.project_mutation import update_project_from_payload

    result = asyncio.run(update_project_from_payload({
        "project_id": project.id,
        "add_browser_url": {"url": " http://a.test ", "label": " A ", "set_default": True},
    }))
    assert result.success
    assert Project.objects.get(id=project.id).browser_urls == [
        {"url": "http://a.test", "label": "A", "default": True},
    ]

    result = asyncio.run(update_project_from_payload({
        "project_id": project.id,
        "add_browser_url": {"url": "javascript:alert(1)"},
    }))
    assert not result.success
    assert result.errors[0].code == "invalid_value"


# ---------------------------------------------------------------------------
# Workspace atomic ops (workspaces.json)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)

    async def _noop():
        return None

    monkeypatch.setattr("twicc.workspaces._broadcast_after_write", _noop)
    return data_dir


def test_read_workspaces_migrates_legacy_browser_url(isolated_data_dir):
    (isolated_data_dir / "workspaces.json").write_bytes(orjson.dumps({
        "workspaces": [{"id": "w1", "name": "W1", "browserUrl": "http://a.test"}],
    }))
    data = read_workspaces()
    assert data["workspaces"][0]["browserUrls"] == [{"url": "http://a.test", "default": True}]
    assert "browserUrl" not in data["workspaces"][0]


def test_workspace_create_and_update_ops(isolated_data_dir):
    from twicc.workspaces import create_workspace_atomic, update_workspace_atomic

    result = asyncio.run(create_workspace_atomic(
        name="Web",
        browser_urls=[{"url": "http://a.test", "default": True}],
    ))
    assert result.success
    ws_id = result.workspace_id
    assert result.workspace["browserUrls"] == [{"url": "http://a.test", "default": True}]

    result = asyncio.run(update_workspace_atomic(
        ws_id,
        add_browser_url={"url": "http://b.test", "label": "B", "set_default": True},
    ))
    assert result.success
    assert result.workspace["browserUrls"] == [
        {"url": "http://a.test"},
        {"url": "http://b.test", "label": "B", "default": True},
    ]

    result = asyncio.run(update_workspace_atomic(
        ws_id, set_default_browser_url="http://absent.test",
    ))
    assert not result.success
    assert result.errors[0].code == "url_not_found"

    result = asyncio.run(update_workspace_atomic(ws_id, remove_browser_url="http://b.test"))
    assert result.success
    assert result.workspace["browserUrls"] == [{"url": "http://a.test"}]

    result = asyncio.run(update_workspace_atomic(ws_id, clear_browser_urls=True))
    assert result.success
    assert result.workspace["browserUrls"] == []


def test_workspace_update_migrates_legacy_key_in_place(isolated_data_dir):
    from twicc.workspaces import update_workspace_atomic

    (isolated_data_dir / "workspaces.json").write_bytes(orjson.dumps({
        "workspaces": [{"id": "w1", "name": "W1", "projectIds": [], "browserUrl": "http://a.test"}],
    }))
    result = asyncio.run(update_workspace_atomic(
        "w1", add_browser_url={"url": "http://b.test", "label": None, "set_default": False},
    ))
    assert result.success
    assert result.workspace["browserUrls"] == [
        {"url": "http://a.test", "default": True},
        {"url": "http://b.test"},
    ]
    assert "browserUrl" not in result.workspace
