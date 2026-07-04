"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""

from twicc.providers.helpers import AGENT_SETTINGS_HIDDEN_FROM_FRONTEND, AgentSettings, get_provider_helpers


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "directory": project.directory,
        "git_root": project.git_root,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "stale": project.stale,
        "name": project.name,
        "color": project.color,
        "archived": project.archived,
        "total_cost": float(project.total_cost) if project.total_cost else None,
        # Non-null => this project is a git worktree; the value is the id of its
        # main repository's project (or None when it is not a worktree). We read
        # the raw FK id (``worktree_of_id``) rather than ``worktree_of`` to avoid
        # the lazy relationship load — keeping this serializer query-free and
        # safe to call from async contexts (see module docstring).
        "worktree_of": project.worktree_of_id,
        # Cross-provider trust: True/False = explicit decision, None = no own
        # decision (the front resolves the effective value by walking ancestors
        # / the worktree_of link). ``trust_imported`` is internal bookkeeping
        # and intentionally not exposed.
        "trust": project.trust,
        "trust_propagation": project.trust_propagation,
        # Per-project agent settings defaults (optional). default_provider is
        # the provider a new session defaults to (None = inherit). The front
        # resolves both by walking the project chain (worktree_of / path
        # ancestors); see frontend/src/utils/projectAgentDefaults.js.
        "default_provider": project.default_provider,
        "default_agent_settings": project.default_agent_settings,
        # Per-project default layout id (named-layout id / "single-pane" / None=inherit).
        "default_layout_id": project.default_layout_id,
        # Absolute base directory for new git worktrees of this project (None =
        # inherit the global worktreeDirectoryTemplate expanded against the project).
        "worktree_directory": project.worktree_directory,
    }


def serialize_artifact_bookmark(bookmark):
    """Serialize an ArtifactBookmark to a dict. Pure: no DB queries — FK ids via
    *_id, root computed from the session id (no relationship access)."""
    import os
    from twicc.paths import get_session_artifacts_dir

    rel = bookmark.relative_path
    return {
        "id": bookmark.id,
        "name": bookmark.name,
        "scope": bookmark.scope,
        "session_id": bookmark.session_id,
        "project_id": bookmark.project_id,
        "relative_path": rel,
        "root": str(get_session_artifacts_dir(bookmark.session_id)),
        "file_ext": os.path.splitext(rel)[1].lstrip(".").lower(),
        "allowed_hosts": bookmark.allowed_hosts,
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "updated_at": bookmark.updated_at.isoformat() if bookmark.updated_at else None,
    }


def serialize_session(session):
    """
    Serialize a Session model to a dictionary.

    Includes ``compute_version_up_to_date`` boolean to indicate if the session's
    metadata has been computed with the current version of rules. The reference
    version comes from the owning provider's ``current_compute_version`` so
    bumping one provider's compute does not invalidate sessions of another;
    for providers without a compute pipeline (``current_compute_version=None``)
    sessions match their default ``compute_version=NULL`` and are reported
    up-to-date.

    Works for both regular sessions and subagents. For subagents,
    parent_session_id will be set; for regular sessions it will be None.

    For the title field, pending titles take priority over the database value.
    This ensures that when a session is created with a custom title, the title
    is immediately visible even before it's written to the JSONL file.
    """
    from twicc.pending_titles import get_pending_title
    from twicc.artifacts_watcher import session_has_artifacts
    from twicc.paths import get_session_artifacts_dir

    # Use pending title if available, otherwise use the stored title
    title = get_pending_title(session.id) or session.title

    # O(1) in-memory read of the ArtifactsWatcher set (no I/O — safe here per
    # the module docstring's "no DB / no I/O" contract).
    has_artifacts = session_has_artifacts(session.id)

    provider_helpers = get_provider_helpers(session.provider)

    return {
        "id": session.id,
        "project_id": session.project_id,
        "provider": session.provider,  # Backend provider (see Provider enum)
        "parent_session_id": session.parent_session_id,  # None for regular sessions, set for subagents
        "last_line": session.last_line,
        "mtime": session.mtime,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_started_at": session.last_started_at.isoformat() if session.last_started_at else None,
        "last_updated_at": session.last_updated_at.isoformat() if session.last_updated_at else None,
        "last_stopped_at": session.last_stopped_at.isoformat() if session.last_stopped_at else None,
        "last_new_content_at": session.last_new_content_at.isoformat() if session.last_new_content_at else None,
        "last_viewed_at": session.last_viewed_at.isoformat() if session.last_viewed_at else None,
        "stale": session.stale,
        "title": title,  # Session title (from pending, first user message, or custom-title)
        # Provider-supplied short identifier — Codex stores the agent_nickname
        # of a subagent here (e.g. "Bohr"), the frontend uses it for the
        # subagent tab labels and the SessionHeader name.
        "slug": session.slug,
        "user_message_count": session.user_message_count,  # Number of user messages (message turns)
        # Boolean indicating if session metadata is up-to-date for the owning provider
        "compute_version_up_to_date": session.compute_version == provider_helpers.current_compute_version,
        # Cost and context usage fields
        "context_usage": session.context_usage,  # Current context usage in tokens
        "self_cost": float(session.self_cost) if session.self_cost else None,  # Own items cost in USD
        "subagents_cost": float(session.subagents_cost) if session.subagents_cost else None,  # Sum of subagents cost
        "total_cost": float(session.total_cost) if session.total_cost else None,  # Total cost in USD
        # Runtime environment fields
        "cwd": session.cwd,  # Current working directory
        "git_branch": session.git_branch or (session.cwd_git_branch if session.git_directory else None),  # Resolved branch, fallback to cwd
        "git_directory": session.git_directory,  # Resolved git root directory
        "model": provider_helpers.serialize_model(session.model),  # Model info object
        # User-controlled fields
        "archived": session.archived,  # Whether the session is archived
        "pinned": session.pinned,  # Whether the session is pinned
        "layout": session.layout,  # Per-session dockable-layout intention (UI state; {} = single pane)
        "tasks": session.tasks,  # Latest task/todo/plan snapshot (common shape; {} = none). Not consumed by the UI yet
        # Plan-like documents touched by the session (list; [] = none) —
        # emitted verbatim, honoring this serializer's query-free contract:
        # relative entries are resolved client-side against the project
        # directory (with the ``worktree_of`` parent as fallback). Drives the
        # Plan tab; see ``Session.plan_paths``.
        "plan_paths": session.plan_paths,
        "goals": session.goals,  # Goal lifecycle history (list; [] = none). Last entry drives the footer goal bar

        "hybrid": session.hybrid,  # Hybrid CLI mode (one-way, see models.Session.hybrid)
        # Closed AgentSettings bundle (cross-provider). Fields listed in
        # ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` are filtered out so they
        # never leak to the frontend.
        **{
            field: getattr(session, field)
            for field in AgentSettings._fields
            if field not in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND
        },
        # Whether the session has been compacted at least once
        "compacted": session.compacted,
        # Whether the session has at least one Claude Code workflow run
        # (``wf_*.json`` in its ``<session_id>/workflows/`` folder). Monotonic /
        # one-way; latched live by the watcher and backfilled by the background
        # compute. See ``Session.has_workflows``.
        "has_workflows": session.has_workflows,
        # Per-session artifacts (files under <data_dir>/artifacts/<id>/). The
        # frontend shows an Artifacts tab when ``has_artifacts`` is true and
        # mounts it on ``artifacts_dir``. ``has_artifacts`` is monotonic and
        # flips false->true live via the ``artifacts_available`` WS message;
        # ``artifacts_dir`` is only sent when present so list payloads stay lean.
        "has_artifacts": has_artifacts,
        "artifacts_dir": str(get_session_artifacts_dir(session.id)) if has_artifacts else None,
        # Provider plan file (Claude Code: ``~/.claude/plans/<slug>.md``). The
        # frontend shows a read-only Plan tab when ``has_plan`` is true and
        # fetches the markdown from ``/api/sessions/<id>/plan/``. Provider-
        # agnostic: ``session_has_plan`` reads each provider's live plans-watcher
        # set in the server (O(1)); off the server (CLI, background compute) it
        # falls back to an on-disk check so the flag stays accurate there too — a
        # read, within the serializer's no-write contract. NOT monotonic — flips
        # both ways live via the ``plan_available`` / ``plan_gone`` WS messages.
        "has_plan": provider_helpers.session_has_plan(session),
        # Hidden + spawned tree links (cf. hidden-sessions design spec). The
        # frontend never sees a hidden session via REST (filtered server
        # side); the CLI uses these fields when it explicitly opts into
        # hidden listings via --include-hidden / --only-hidden.
        "hidden": session.hidden,
        "spawned_by": session.spawned_by_id,
        "spawn_root": session.spawn_root_id,
        "annotations": session.annotations,
    }


def serialize_usage_snapshot(snapshot, period_costs=None, references=None):
    """
    Serialize a UsageSnapshot model to a dictionary.

    Sends raw stored data — the frontend computes derived values
    (temporal %, burn rate, levels).

    Args:
        snapshot: UsageSnapshot model instance.
        period_costs: Optional dict with "five_hour" and "seven_day" cost data
            from compute_period_costs(). Each contains spent, estimated_period,
            estimated_monthly.
        references: Optional dict with historical reference snapshots for
            recent burn rate computation (keys: "one_hour", "one_day").
    """
    def _fmt_dt(dt):
        return dt.isoformat() if dt else None

    data = {
        "provider": snapshot.provider,
        "fetched_at": _fmt_dt(snapshot.fetched_at),
        # Five-hour quota
        "five_hour_utilization": snapshot.five_hour_utilization,
        "five_hour_resets_at": _fmt_dt(snapshot.five_hour_resets_at),
        # Seven-day global quota
        "seven_day_utilization": snapshot.seven_day_utilization,
        "seven_day_resets_at": _fmt_dt(snapshot.seven_day_resets_at),
        # Extra usage
        "extra_usage_is_enabled": snapshot.extra_usage_is_enabled,
        "extra_usage_monthly_limit": snapshot.extra_usage_monthly_limit,
        "extra_usage_used_credits": snapshot.extra_usage_used_credits,
        "extra_usage_utilization": snapshot.extra_usage_utilization,
        "extra_usage_remaining_credits": snapshot.extra_usage_remaining_credits,
    }

    # Period cost data (spent, estimated_period, estimated_monthly)
    if period_costs:
        data["period_costs"] = period_costs

    # Reference snapshots for recent burn rate computation
    if references:
        data["references"] = references

    return data


def serialize_session_item(item):
    """
    Serialize a SessionItem model to a dictionary with full content.

    Used by:
    - GET /api/.../items/?range=... endpoint
    - WebSocket item_created messages
    """
    return {
        "line_num": item.line_num,
        "content": item.content,
        # Display metadata fields
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
        # Item timestamp (ISO 8601, UTC) — the moment the provider wrote this
        # JSONL line. Surfaced so the frontend can place per-block day separators
        # without parsing the raw content (and even before content is loaded).
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
    }


def serialize_session_item_metadata(item):
    """
    Serialize a SessionItem model to a dictionary WITHOUT content.

    Used by:
    - GET /api/.../items/metadata/ endpoint

    This is a lightweight serialization for loading all item metadata
    without the potentially large content field.
    """
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
        "git_directory": item.git_directory,
        "git_branch": item.git_branch,
        # Item timestamp (ISO 8601, UTC) — surfaced at the metadata level so the
        # frontend has it for every item up front (content stays lazy-loaded),
        # which is what per-block day separators need.
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
        # NO content field - that's the whole point
    }
