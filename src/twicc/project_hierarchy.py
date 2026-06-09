"""Project hierarchy walk for inherited per-project agent-settings defaults.

Builds the ordered ancestor chain of a project — worktree main repo first, else
nearest path ancestor, recursively — and resolves the per-project agent-settings
defaults by walking that chain field by field.

Unlike trust (:mod:`twicc.trust`) there is **no propagation gate**: every project
in the chain is visited and, for each :class:`~twicc.providers.helpers.AgentSettings`
field, the first non-``None`` value wins. The chain is also **unfiltered** — an
ancestor with no own defaults is still a step on the way up, because a different
field may be set further up.

The DB-stored value is ``Project.default_agent_settings``, a per-provider map::

    { "<provider>": { "<AgentSettings field>": value | null, ... }, ... }

A ``null``/absent field inherits from the parent chain (and ultimately the global
synced defaults, handled later by ``resolve_agent_settings``). This resolution is
mirrored for display in ``frontend/src/utils/projectAgentDefaults.js``; keep the
two in sync.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple


class ProjectDefaultsRow(NamedTuple):
    """The hierarchy-relevant slice of a ``Project``, loaded once for resolution."""

    id: str
    directory: str | None
    worktree_of_id: str | None
    default_provider: str | None
    default_agent_settings: dict | None


def _segments(path: str) -> list[str]:
    """Normalized, non-empty path segments (pure — no filesystem access)."""
    return [s for s in os.path.normpath(path).split(os.sep) if s]


def _is_path_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff *ancestor* is a strict directory prefix of *descendant*.

    Compared segment-by-segment (not ``str.startswith``) so ``/a/b`` is not an
    ancestor of ``/a/bc``. Both paths are assumed already realpath-normalized
    (``Project.directory`` is stored realpath'd); ``normpath`` only tidies them.
    """
    a, d = _segments(ancestor), _segments(descendant)
    return len(a) < len(d) and d[: len(a)] == a


def _nearest_path_ancestor(
    target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]
) -> ProjectDefaultsRow | None:
    """The nearest (longest-path) ancestor of *target*.

    Unlike trust's variant this is **unfiltered**: any registered project that is
    a strict path-prefix of *target* qualifies, regardless of whether it has its
    own defaults — a higher ancestor may set a field this one leaves unset.
    """
    if not target.directory:
        return None
    best: ProjectDefaultsRow | None = None
    best_len = -1
    for row in rows:
        if row.id == target.id or not row.directory:
            continue
        if _is_path_ancestor(row.directory, target.directory):
            depth = len(_segments(row.directory))
            if depth > best_len:
                best, best_len = row, depth
    return best


def ancestor_chain(
    target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]
) -> list[ProjectDefaultsRow]:
    """Ordered chain ``[target, parent, grandparent, ...]``.

    At each node the parent is its ``worktree_of`` main repo if set, else its
    nearest path ancestor. After jumping to a worktree's main repo, the walk
    continues by path from that repo (and ``worktree_of`` regains priority at any
    node that is itself a worktree). Recurses with a cycle guard against
    pathological ``worktree_of`` loops.
    """
    by_id = {r.id: r for r in rows}
    chain: list[ProjectDefaultsRow] = []
    seen: set[str] = set()
    node: ProjectDefaultsRow | None = target
    while node is not None and node.id not in seen:
        chain.append(node)
        seen.add(node.id)
        if node.worktree_of_id:
            node = by_id.get(node.worktree_of_id)
        else:
            node = _nearest_path_ancestor(node, rows)
    return chain


def resolve_project_agent_settings(
    project_id: str, provider: str, rows: list[ProjectDefaultsRow]
) -> dict[str, Any]:
    """Per-field first-non-``None`` agent-settings values from the chain, for *provider*.

    Returns only the fields the chain actually sets; a missing field means "the
    project layer has nothing to say — inherit further / fall to the global
    synced default" (that fallback is applied by ``resolve_agent_settings``, not
    here). Values are returned verbatim and are NOT validated.
    """
    target = next((r for r in rows if r.id == project_id), None)
    if target is None:
        return {}
    resolved: dict[str, Any] = {}
    for row in ancestor_chain(target, rows):
        bundle = (row.default_agent_settings or {}).get(provider) or {}
        for field, value in bundle.items():
            # First non-None wins (nearest ancestor); an explicit null means
            # "inherit", so it does not block a value set further up.
            if value is not None and field not in resolved:
                resolved[field] = value
    return resolved


# --- DB-facing helpers ----------------------------------------------------


def load_defaults_rows() -> list[ProjectDefaultsRow]:
    """Load the defaults-relevant slice of every project (sync DB access)."""
    from twicc.core.models import Project

    return [
        ProjectDefaultsRow(
            id=row["id"],
            directory=row["directory"],
            worktree_of_id=row["worktree_of_id"],
            default_provider=row["default_provider"],
            default_agent_settings=row["default_agent_settings"],
        )
        for row in Project.objects.values(
            "id", "directory", "worktree_of_id",
            "default_provider", "default_agent_settings",
        )
    ]


def project_agent_defaults(project_id: str | None, provider: str) -> dict[str, Any]:
    """Resolve one project's inherited agent-settings defaults for *provider* (sync).

    Loads the live project table and walks the chain. Returns ``{}`` when
    ``project_id`` is falsy or unknown. Async callers must wrap this in
    ``sync_to_async`` — it performs ORM queries.
    """
    if not project_id:
        return {}
    rows = load_defaults_rows()
    return resolve_project_agent_settings(project_id, provider, rows)
