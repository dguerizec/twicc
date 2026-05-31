"""``twicc topology`` — inspect the spawned-session tree around a session."""

from __future__ import annotations

from decimal import Decimal
import sys

import orjson


def main(session_id: str, *, include_processes: bool = True) -> None:
    """Emit the spawned-session tree containing ``session_id`` as JSON."""
    import django

    django.setup()

    from twicc.core.models import SessionType

    seed = _resolve_seed(session_id)
    if seed.type != SessionType.SESSION:
        print(
            f"Error: session '{seed.id}' is a subagent; topology follows "
            "spawned_by, not parent_session_id.",
            file=sys.stderr,
        )
        sys.exit(1)

    data = build_topology(seed, include_processes=include_processes)
    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def build_topology(
    seed,
    *,
    include_processes: bool = True,
    twicc_pid: int | None = None,
) -> dict:
    """Build the spawned-session topology containing ``seed``.

    ``twicc_pid`` is injectable for tests. When omitted and process data is
    requested, the live TwiCC sidecar is resolved; if unavailable, topology is
    still returned with process data marked unavailable.
    """
    from twicc.core.models import ProcessRun, Session, SessionType

    ancestors, cycle_detected = _collect_ancestors(seed)
    root = ancestors[0]

    sessions_by_id: dict[str, Session] = {
        session.id: session
        for session in ancestors
    }
    children_by_parent: dict[str, list[str]] = {}
    frontier = [root.id]
    seen = {root.id}

    while frontier:
        children = list(
            Session.objects
            .filter(type=SessionType.SESSION, spawned_by_id__in=frontier)
            .order_by("created_at", "id")
        )
        next_frontier = []
        for child in children:
            if child.id in seen:
                cycle_detected = True
                continue
            seen.add(child.id)
            sessions_by_id[child.id] = child
            children_by_parent.setdefault(child.spawned_by_id, []).append(child.id)
            next_frontier.append(child.id)
        frontier = next_frontier

    tree = _build_tree(root.id, children_by_parent)
    ordered_ids = list(_walk_tree_ids(tree))
    metrics_by_id = _compute_node_metrics(
        root.id,
        children_by_parent,
        sessions_by_id,
    )

    process_rows_by_id: dict[str, ProcessRun] = {}
    processes_available = False
    if include_processes:
        if twicc_pid is None:
            from twicc.cli._twicc_info import resolve_live_twicc

            info = resolve_live_twicc()
            twicc_pid = info.pid if info is not None else None
        if twicc_pid is not None:
            processes_available = True
            process_rows_by_id = _load_process_rows(ordered_ids, twicc_pid)

    nodes = [
        _serialize_topology_node(
            sessions_by_id[session_id],
            process_rows_by_id.get(session_id),
            processes_available=processes_available,
            metrics=metrics_by_id[session_id],
        )
        for session_id in ordered_ids
    ]

    return {
        "seed_session_id": seed.id,
        "root_session_id": root.id,
        "path_to_seed": [session.id for session in ancestors],
        "cycle_detected": cycle_detected,
        "processes": {
            "requested": include_processes,
            "available": processes_available,
            "reason": _processes_reason(include_processes, processes_available),
        },
        "tree": tree,
        "nodes": nodes,
    }


def _resolve_seed(session_id: str):
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.core.models import Session

    if session_id == "self":
        session = resolve_current_session()
        if session is None:
            print(
                "Error: self could not be resolved: no TwiCC session found in PID ancestry.",
                file=sys.stderr,
            )
            sys.exit(1)
        return session

    try:
        return Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        print(f"Error: session '{session_id}' not found.", file=sys.stderr)
        sys.exit(1)


def _collect_ancestors(seed) -> tuple[list, bool]:
    """Return ``[root, ..., seed]`` following ``spawned_by`` links."""
    current = seed
    ancestors = [seed]
    seen = {seed.id}
    cycle_detected = False

    while current.spawned_by_id is not None:
        if current.spawned_by_id in seen:
            cycle_detected = True
            break
        parent = _get_session(current.spawned_by_id)
        if parent is None:
            break
        ancestors.append(parent)
        seen.add(parent.id)
        current = parent

    ancestors.reverse()
    return ancestors, cycle_detected


def _get_session(session_id: str):
    from twicc.core.models import Session, SessionType

    return (
        Session.objects
        .filter(id=session_id, type=SessionType.SESSION)
        .first()
    )


def _build_tree(session_id: str, children_by_parent: dict[str, list[str]]) -> dict:
    return {
        "id": session_id,
        "children": [
            _build_tree(child_id, children_by_parent)
            for child_id in children_by_parent.get(session_id, [])
        ],
    }


def _walk_tree_ids(node: dict):
    yield node["id"]
    for child in node["children"]:
        yield from _walk_tree_ids(child)


def _compute_node_metrics(
    root_id: str,
    children_by_parent: dict[str, list[str]],
    sessions_by_id: dict,
) -> dict[str, dict]:
    metrics_by_id = {}

    def visit(session_id: str) -> tuple[int, Decimal, bool]:
        direct_children = children_by_parent.get(session_id, [])
        descendant_count = 0
        total_cost = Decimal(0)
        has_cost = False

        session_cost = sessions_by_id[session_id].total_cost
        if session_cost is not None:
            total_cost += session_cost
            has_cost = True

        for child_id in direct_children:
            child_descendant_count, child_total_cost, child_has_cost = visit(child_id)
            descendant_count += 1 + child_descendant_count
            if child_has_cost:
                total_cost += child_total_cost
                has_cost = True

        metrics_by_id[session_id] = {
            "direct_child_count": len(direct_children),
            "descendant_count": descendant_count,
            "subtree_total_cost": float(total_cost) if has_cost else None,
        }
        return descendant_count, total_cost, has_cost

    visit(root_id)
    return metrics_by_id


def _load_process_rows(session_ids: list[str], twicc_pid: int) -> dict:
    from twicc.core.models import ProcessRun

    rows_by_id = {}
    for row in (
        ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id__in=session_ids)
        .order_by("session_id", "-started_at")
    ):
        if row.session_id not in rows_by_id:
            rows_by_id[row.session_id] = row
    return rows_by_id


def _serialize_topology_node(
    session,
    process_row,
    *,
    processes_available: bool,
    metrics: dict,
) -> dict:
    from twicc.core.serializers import serialize_session

    return {
        "id": session.id,
        "session": serialize_session(session),
        "process": _serialize_process(process_row, processes_available=processes_available),
        **metrics,
    }


def _serialize_process(row, *, processes_available: bool) -> dict | None:
    if not processes_available:
        return None

    if row is None:
        return {
            "id": None,
            "state": "dead",
            "started_at": None,
            "last_state_change_at": None,
            "pid": None,
        }

    from twicc.cli._process_state import project_virtual_state

    return {
        "id": row.pk,
        "state": project_virtual_state(row),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_state_change_at": (
            row.last_state_change_at.isoformat()
            if row.last_state_change_at
            else None
        ),
        "pid": row.agent_pid,
    }


def _processes_reason(requested: bool, available: bool) -> str | None:
    if not requested:
        return "not_requested"
    if not available:
        return "twicc_not_running"
    return None
