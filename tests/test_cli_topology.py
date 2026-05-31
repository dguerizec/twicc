"""Tests for the spawned-session topology builder."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli.topology import build_topology
from twicc.core.models import ProcessRun, Project, Session, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-topology",
        directory="/tmp/twicc-topology",
    )


def make_session(
    project,
    session_id: str,
    *,
    title: str,
    spawned_by=None,
    minutes: int = 0,
    **overrides,
):
    now = timezone.now() + timedelta(minutes=minutes)
    values = {
        "id": session_id,
        "project": project,
        "provider": "codex",
        "file_path": f"{session_id}.jsonl",
        "type": SessionType.SESSION,
        "title": title,
        "created_at": now,
        "last_new_content_at": now,
        "user_message_count": 1,
        "spawned_by": spawned_by,
    }
    values.update(overrides)
    return Session.objects.create(**values)


def test_build_topology_returns_rooted_tree_from_middle_node(project):
    root = make_session(project, "A", title="Root", minutes=0)
    root.total_cost = Decimal("1.000000")
    root.spawn_root = root
    root.annotations = {"role": "coordinator"}
    root.save(update_fields=["total_cost", "spawn_root", "annotations"])
    b = make_session(
        project,
        "B",
        title="Backend",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
        total_cost=Decimal("2.000000"),
    )
    c = make_session(
        project,
        "C",
        title="Frontend",
        spawned_by=root,
        spawn_root=root,
        minutes=2,
    )
    make_session(
        project,
        "D",
        title="Tests",
        spawned_by=b,
        spawn_root=root,
        minutes=3,
        total_cost=Decimal("0.500000"),
    )
    make_session(
        project,
        "E",
        title="Hidden",
        spawned_by=root,
        spawn_root=root,
        minutes=4,
        hidden=True,
        archived=True,
    )

    data = build_topology(c, include_processes=False)

    assert data["seed_session_id"] == "C"
    assert data["root_session_id"] == "A"
    assert data["path_to_seed"] == ["A", "C"]
    assert data["total_cost"] == 3.5
    assert data["node_count"] == 5
    assert data["processes"] == {
        "requested": False,
        "available": False,
        "reason": "not_requested",
    }
    assert list(data.keys()) == [
        "seed_session_id",
        "root_session_id",
        "path_to_seed",
        "cycle_detected",
        "total_cost",
        "node_count",
        "processes",
        "tree",
        "nodes",
    ]
    assert data["tree"] == {
        "id": "A",
        "children": [
            {"id": "B", "children": [{"id": "D", "children": []}]},
            {"id": "C", "children": []},
            {"id": "E", "children": []},
        ],
    }
    nodes = {node["id"]: node for node in data["nodes"]}
    assert list(nodes) == ["A", "B", "D", "C", "E"]
    assert nodes["A"]["session"]["id"] == "A"
    assert nodes["A"]["session"]["context_usage"] is None
    assert nodes["A"]["session"]["spawn_root"] == "A"
    assert nodes["A"]["session"]["annotations"] == {"role": "coordinator"}
    assert nodes["B"]["session"]["spawn_root"] == "A"
    assert nodes["A"]["direct_child_count"] == 3
    assert nodes["A"]["descendant_count"] == 4
    assert nodes["A"]["subtree_total_cost"] == 3.5
    assert nodes["B"]["direct_child_count"] == 1
    assert nodes["B"]["descendant_count"] == 1
    assert nodes["B"]["subtree_total_cost"] == 2.5
    assert nodes["C"]["subtree_total_cost"] is None
    assert nodes["E"]["session"]["hidden"] is True
    assert nodes["E"]["session"]["archived"] is True


def test_build_topology_adds_compact_process_state(project):
    root = make_session(project, "A", title="Root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    child = make_session(
        project,
        "B",
        title="Worker",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
    )
    now = timezone.now()
    ProcessRun.objects.create(
        provider="codex",
        session_id=child.id,
        started_at=now,
        state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now,
        twicc_pid=1234,
        agent_pid=5678,
        awaiting_user_input=True,
    )

    data = build_topology(root, include_processes=True, twicc_pid=1234)
    nodes = {node["id"]: node for node in data["nodes"]}

    assert data["processes"] == {
        "requested": True,
        "available": True,
        "reason": None,
    }
    assert nodes["A"]["process"]["state"] == "dead"
    assert nodes["B"]["process"]["state"] == "awaiting_user_input"
    assert nodes["B"]["process"]["pid"] == 5678


def test_build_topology_marks_processes_unavailable_without_live_twicc(project, monkeypatch):
    root = make_session(project, "A", title="Root")
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)

    data = build_topology(root, include_processes=True)

    assert data["processes"] == {
        "requested": True,
        "available": False,
        "reason": "twicc_not_running",
    }
    assert data["nodes"][0]["process"] is None
