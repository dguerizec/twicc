"""descendant_ids: the spawn-subtree resolver shared by the CLI filters and
the share agent gate (design §6)."""

import pytest

from twicc.core.models import Project, Session, SessionType


def _mk(project, sid, *, spawned_by=None, spawn_root=None, parent_session=None):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code",
        file_path=f"{sid}.jsonl",
        type=SessionType.SUBAGENT if parent_session else SessionType.SESSION,
        spawned_by=spawned_by, spawn_root=spawn_root, parent_session=parent_session,
    )


def _tree(db):
    project = Project.objects.create(id="-tmp-scope", directory="/tmp/scope")
    root = _mk(project, "root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    a = _mk(project, "a", spawned_by=root, spawn_root=root)
    b = _mk(project, "b", spawned_by=a, spawn_root=root)
    c = _mk(project, "c", spawned_by=root, spawn_root=root)
    return project, root, a, b, c


def test_descendants_of_root(transactional_db):
    _tree(transactional_db)
    from twicc.core.services.spawn_scope import descendant_ids

    assert descendant_ids("root") == {"a", "b", "c"}


def test_descendants_of_mid_tree_branch_only(transactional_db):
    _tree(transactional_db)
    from twicc.core.services.spawn_scope import descendant_ids

    assert descendant_ids("a") == {"b"}


def test_leaf_and_lone_session_have_no_descendants(transactional_db):
    project, *_ = _tree(transactional_db)
    _mk(project, "lone")
    from twicc.core.services.spawn_scope import descendant_ids

    assert descendant_ids("b") == set()
    assert descendant_ids("lone") == set()


def test_unknown_id_is_empty(transactional_db):
    from twicc.core.services.spawn_scope import descendant_ids

    assert descendant_ids("nope") == set()


def test_claude_subagent_is_not_a_descendant(transactional_db):
    """Subagents carry parent_session, not spawned_by — outside the spawn
    tree by design (§6 'Subagents are out')."""
    project, root, *_ = _tree(transactional_db)
    _mk(project, "sub", parent_session=root)
    from twicc.core.services.spawn_scope import descendant_ids

    assert "sub" not in descendant_ids("root")


def test_resolve_descendants_filter_delegates(monkeypatch):
    """The explicit-id branch calls the shared helper directly."""
    calls = []

    def fake_descendant_ids(session_id):
        calls.append(session_id)
        return {"sentinel"}

    monkeypatch.setattr(
        "twicc.core.services.spawn_scope.descendant_ids", fake_descendant_ids,
    )
    from twicc.cli._drop_request.whoami import resolve_descendants_filter

    assert resolve_descendants_filter("root") == {"sentinel"}
    assert calls == ["root"]


def test_resolve_descendants_filter_keyword_branches(transactional_db, monkeypatch):
    """Both public keywords resolve a target before common delegation."""
    _project, root, a, b, _c = _tree(transactional_db)
    from twicc.cli._drop_request.whoami import resolve_descendants_filter

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: a,
    )
    assert resolve_descendants_filter("self") == {"b"}

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: b,
    )
    assert resolve_descendants_filter("parent") == {"b"}

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: root,
    )
    with pytest.raises(RuntimeError, match="no spawned_by"):
        resolve_descendants_filter("parent")
