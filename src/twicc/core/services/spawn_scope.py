"""Spawn-subtree resolution shared by the CLI ``--descendants`` filter and the
share agent gate (agent-sharing design §6).

Sync ORM — async callers wrap in ``sync_to_async``. On consistently
denormalised ``spawn_root`` data, this preserves the resolver's
proper-descendant contract; unknown ids return an empty set.
"""

from __future__ import annotations

from collections import deque


def descendant_ids(session_id: str) -> set[str]:
    """Proper spawn-tree descendants of ``session_id`` (the session itself is
    never included). Unknown id → empty set. Claude subagents (``parent_session``
    edge, ``spawned_by`` NULL) are not spawn-tree members and never appear."""
    from twicc.core.models import Session

    try:
        session = Session.objects.only("id", "spawn_root_id").get(pk=session_id)
    except Session.DoesNotExist:
        return set()
    target_id = session.id
    tree_key = session.spawn_root_id or session.id

    rows = list(
        Session.objects.filter(spawn_root_id=tree_key).only("id", "spawned_by_id")
    )

    if tree_key == target_id:
        # Target is the tree root → every other row is a descendant.
        return {r.id for r in rows if r.id != target_id}

    # Target is mid-tree → BFS its branch to drop sibling/parent rows.
    adj: dict[str, list[str]] = {}
    for r in rows:
        if r.spawned_by_id:
            adj.setdefault(r.spawned_by_id, []).append(r.id)

    out: set[str] = set()
    queue = deque(adj.get(target_id, ()))
    while queue:
        node = queue.popleft()
        if node in out:
            continue
        out.add(node)
        queue.extend(adj.get(node, ()))
    return out
