"""Per-project default layout: backend chain resolution (creation-only).

Backend mirror of ``frontend/src/utils/layoutDefaults.js``. Reuses the agent-defaults
ancestor walk (``project_agent_defaults``): resolve a project's inherited default layout
*id* up the worktree/path chain (first non-NULL ``default_layout_id`` wins; NULL = inherit),
fall back to the global ``settings.json`` ``defaultLayoutId``, then look the id up in
``layouts.json`` and return its intention.

Runs only when the **CLI** creates a session (the web UI resolves frontend-side and ships the
intention in the create payload). Resolution is creation-time only; a later default change never
touches an existing session. Dangling / unknown ids degrade to single pane (``{}``).
"""

from __future__ import annotations

from twicc.project_agent_defaults import (
    ProjectDefaultsRow,
    ancestor_chain,
    load_defaults_rows,
)

SINGLE_PANE_ID = "single-pane"


def resolve_layout_id(target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]) -> str | None:
    """First explicit ``default_layout_id`` up the chain; ``None`` if nothing sets one (inherit)."""
    for node in ancestor_chain(target, rows):
        if node.default_layout_id is not None:
            return node.default_layout_id
    return None


def _global_default_layout_id() -> str:
    from twicc.synced_settings import read_synced_settings

    return read_synced_settings().get("defaultLayoutId") or SINGLE_PANE_ID


def _intention_for_layout_id(layout_id: str | None) -> dict:
    """The template intention for a layout id; ``{}`` for single pane / unknown (dangling-tolerant)."""
    if not layout_id or layout_id == SINGLE_PANE_ID:
        return {}
    from twicc.layouts import read_layouts

    for layout in read_layouts().get("layouts", []):
        if layout.get("id") == layout_id:
            intention = layout.get("intention")
            return intention if isinstance(intention, dict) else {}
    return {}


def resolve_project_layout_default(project_id: str | None, *, directory: str | None = None) -> dict:
    """Resolve a project's inherited default layout intention against the live DB + layouts.json.

    Synchronous DB access (call via ``sync_to_async`` from async code).
    """
    rows = load_defaults_rows()
    target = next((r for r in rows if r.id == project_id), None)
    if target is None:
        target = ProjectDefaultsRow(
            id=project_id or "",
            directory=directory,
            worktree_of_id=None,
            default_provider=None,
            default_agent_settings=None,
            default_layout_id=None,
        )
    layout_id = resolve_layout_id(target, rows)
    if layout_id is None:
        layout_id = _global_default_layout_id()
    return _intention_for_layout_id(layout_id)
