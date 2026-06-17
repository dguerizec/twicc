from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import cached_property
from typing import ClassVar, TypeAlias

from django.db import models

import logging

from django.db.models import Q

from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind, Provider

# Django ``choices`` derived from :class:`AgentState` so the runtime enum and
# the DB column stay in lockstep without redeclaring the values here. Labels
# match what Django's :class:`models.TextChoices` would auto-generate ("STARTING"
# → "Starting", "ASSISTANT_TURN" → "Assistant Turn", ...).
_AGENT_STATE_CHOICES = [
    (state.value, state.name.replace('_', ' ').title())
    for state in AgentState
]

logger = logging.getLogger(__name__)


def cron_occurrences(cron_expr: str, from_dt: datetime):
    """Iterate cron occurrences from a given datetime.

    CronSim expects local time (cron expressions are in local time), so this function
    converts the input datetime from UTC to local time before passing it to CronSim,
    then converts each result back to UTC.

    Args:
        cron_expr: A 5-field cron expression (e.g., "0 9 * * *")
        from_dt: Start datetime (UTC). Occurrences will be after this time.

    Yields:
        datetime objects in UTC for each cron occurrence.
    """
    from cronsim import CronSim

    local_from = from_dt.astimezone()  # Convert UTC → local
    for occurrence in CronSim(cron_expr, local_from):
        yield occurrence.astimezone(timezone.utc)

DateType: TypeAlias = date


class SessionType(models.TextChoices):
    """Type of session entry."""
    SESSION = "session", "Session"
    SUBAGENT = "subagent", "Subagent"


class PinMode(models.TextChoices):
    """Pin visibility scope for a session."""
    PROJECT = "project", "Project"
    WORKSPACE = "workspace", "Workspace"
    ALL = "all", "All projects"


class Project(models.Model):
    """A project corresponds to a working directory.

    The ``id`` is derived from the directory path via
    :func:`twicc.paths.path_to_project_id` — a convention inherited from
    Claude Code but reused as the cross-provider TwiCC project key.
    Multiple providers can run inside the same project, sharing this ID.
    """

    id = models.CharField(max_length=255, primary_key=True)
    directory = models.CharField(max_length=500, null=True, blank=True)  # Actual filesystem path (from session cwd)
    git_root = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory (walking up from directory)
    sessions_count = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    stale = models.BooleanField(default=False)  # True if folder/file no longer exists on disk
    name = models.CharField(max_length=25, null=True, blank=True, unique=True)  # User-defined display name
    color = models.CharField(max_length=50, null=True, blank=True)  # CSS color value (hex, hsl, etc.)
    archived = models.BooleanField(default=False)  # User can archive projects to hide from default list
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)  # Sum of all sessions total_cost in USD
    # Non-null => this project is a git worktree; the link points at its main
    # repository's project. Set explicitly at registration when the caller
    # knows the parent (worktree-creation endpoint), otherwise detected by
    # twicc.projects.ensure_worktree_link right after a project's workspace
    # auto-add, and backfilled for pre-existing rows by the
    # backfill_worktree_links management command.
    worktree_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="worktrees",
    )
    # ---- Cross-provider trust ---------------------------------------
    # The DB is the source of truth; the provider configs (~/.claude.json,
    # ~/.codex/config.toml) are a write-mostly projection. The effective trust
    # of a NULL (inherited) project is always computed live, never stored.
    # See docs/plans/2026-06-09-project-trust-design.md.
    #
    # True = trusted (explicit decision), False = untrusted (explicit decision),
    # NULL = no own decision → inherited from an ancestor / worktree main repo,
    # or asked at the session gate.
    trust = models.BooleanField(null=True, blank=True, default=None)
    # Whether an explicit trust decision cascades to NULL descendants. The
    # default is set at decision time (= "project is under git"); the column
    # default is False. Meaningful only when ``trust`` is non-NULL.
    trust_propagation = models.BooleanField(default=False)
    # Bookkeeping: True once TwiCC has handled this project's trust at least once
    # at the gate (resolved / imported / decided / materialized). Once True, the
    # provider config is never re-read for this project — the DB is authoritative.
    # This is what prevents a materialized provider entry from being re-adopted
    # as a fresh decision after an inheritance change.
    trust_imported = models.BooleanField(default=False)
    # ---- Per-project agent settings defaults ------------------------
    # Optional defaults that seed a NEW session's agent settings, inherited UP
    # the project hierarchy (worktree_of main repo first, else nearest path
    # ancestor, recursively). These are a CREATION-TIME concern only: the
    # chain is resolved when a session is being created and materialized into
    # concrete values frozen onto the session at launch. A launched session is
    # a snapshot — changing a project (or global) default never affects it.
    # The chain resolution lives in frontend/src/utils/projectAgentDefaults.js
    # (draft pre-fill) and its backend mirror twicc/project_agent_defaults.py
    # (CLI create-session); it is NEVER wired into the per-turn resolution
    # sites. (resolve_agent_settings still falls a NULL column back to the
    # global synced default, but that only fires for legacy sessions created
    # before the snapshot model.) There is no propagation flag (unlike trust).
    #
    # Which provider a NEW session in this project defaults to. NULL =
    # inherit from the chain, ultimately the global defaultProvider. Only
    # consumed where a provider is picked (the frontend new-session flow and
    # CLI create-session without --provider); an explicit provider always wins.
    default_provider = models.CharField(max_length=50, null=True, blank=True, default=None)
    # Per-provider partial agent-settings bundles:
    #   { "<provider>": { "<AgentSettings field>": value | null, ... }, ... }
    # A field that is NULL/absent inherits from the parent chain. Storing one
    # bundle PER provider (not just default_provider's) lets a session created
    # with a non-default provider still pick up that provider's project bundle.
    default_agent_settings = models.JSONField(null=True, blank=True, default=None)
    # ---- Worktree creation ------------------------------------------
    # Absolute base directory under which new git worktrees of this project
    # are created from the UI. Free-form and intentionally unconstrained: a
    # user may keep worktrees anywhere, not necessarily under git_root.
    # NULL/empty = no project-level choice, so the worktree-create dialog
    # falls back to the global ``defaultWorktreeDirectory`` synced setting,
    # composed against this project's git_root. Editable from the web dialog
    # and the CLI (`update-project --worktree-directory`); worktree creation
    # itself remains web-UI only.
    worktree_directory = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        ordering = ["-mtime"]

    def recalculate_total_cost(self) -> None:
        """Recalculate total_cost from non-stale SESSION-type sessions."""
        from decimal import Decimal
        from django.db.models import Sum

        total = self.sessions.filter(
            type=SessionType.SESSION,
            total_cost__isnull=False,
        ).aggregate(total=Sum("total_cost"))["total"] or Decimal(0)
        self.total_cost = total if total > 0 else None

    def __str__(self):
        return self.id


class PeriodicActivity(models.Model):
    """Pre-computed periodic activity metrics, per project / provider, and global.

    Each row stores the data for a given date (Monday for week, for example),
    scoped to one backend ``provider``. ``project=NULL`` means global (all
    projects combined). The unique key ``(project, date, provider)`` lets
    several providers share the same project/date without colliding.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
        null=True,
        blank=True,  # NULL = global (all projects)
    )
    provider = models.CharField(max_length=50)  # Backend provider (see Provider enum)
    date = models.DateField()
    user_message_count = models.PositiveIntegerField(default=0)
    session_count = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["project", "date", "provider"],
                name="uniq_pdp_%(class)s",
            ),
        ]

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Return the (start, end) datetime range for the given activity date.

        Must be overridden by subclasses to define the period length.
        """
        raise NotImplementedError

    @classmethod
    def recalculate(
        cls, project_id: str | None, activity_date: DateType, provider: Provider,
    ) -> None:
        """Recalculate activity counters for ``(project, date, provider)`` from the DB.

        Queries SessionItem and Session (filtered by ``provider``) to
        compute accurate values, then sets them via update_or_create
        (idempotent).

        Args:
            project_id: The project ID, or None for global (all projects).
            activity_date: The date (day for DailyActivity, Monday for WeeklyActivity).
            provider: Backend provider enum value. Bounds the aggregation
                to the rows produced by that provider, so each
                ``(project, date, provider)`` triple lives in its own row.
        """
        from django.db.models import Q, Sum

        from twicc.core.enums import ItemKind

        date_start, date_end = cls.date_range(activity_date)

        # Project filter for items (via session FK) and sessions
        item_project_filter = Q(session__project_id=project_id) if project_id is not None else Q()
        session_project_filter = Q(project_id=project_id) if project_id is not None else Q()

        # user_message_count: only from type=SESSION sessions, hidden excluded
        user_message_count = SessionItem.objects.filter(
            item_project_filter,
            session__provider=provider.value,
            kind=ItemKind.USER_MESSAGE,
            timestamp__gte=date_start,
            timestamp__lt=date_end,
            session__type=SessionType.SESSION,
            session__hidden=False,
        ).count()

        # cost: from ALL session types
        cost = SessionItem.objects.filter(
            item_project_filter,
            session__provider=provider.value,
            cost__isnull=False,
            timestamp__gte=date_start,
            timestamp__lt=date_end,
        ).aggregate(total=Sum("cost"))["total"] or Decimal(0)

        # session_count: only type=SESSION sessions with at least one user message, hidden excluded
        session_count = Session.objects.filter(
            session_project_filter,
            provider=provider.value,
            type=SessionType.SESSION,
            created_at__gte=date_start,
            created_at__lt=date_end,
            user_message_count__gt=0,
            hidden=False,
        ).count()

        # If all values are zero, delete the row to keep the table clean
        if user_message_count == 0 and cost == 0 and session_count == 0:
            cls.objects.filter(
                project_id=project_id, date=activity_date, provider=provider.value,
            ).delete()
            return

        cls.objects.update_or_create(
            project_id=project_id,
            date=activity_date,
            provider=provider.value,
            defaults={
                "user_message_count": user_message_count,
                "session_count": session_count,
                "cost": cost,
            },
        )

    @staticmethod
    def recalculate_for_days(
        project_id: str, days: set, provider: Provider, do_global: bool = True,
    ) -> None:
        """Recalculate daily and weekly activity for ``provider`` over ``days``.

        Deduces which weeks (Mondays) are affected from the days, then
        recalculates both project-specific and global rows for the given
        provider by querying SessionItem and Session from the database.

        Args:
            project_id: The project ID.
            days: Set of date objects representing affected days.
            provider: Backend provider enum value.
            do_global: Whether to recalculate global rows.
        """
        if not days:
            return
        mondays = {day - timedelta(days=day.weekday()) for day in days}
        for day in days:
            DailyActivity.recalculate(project_id, day, provider)
            if do_global:
                DailyActivity.recalculate(None, day, provider)
        for monday in mondays:
            WeeklyActivity.recalculate(project_id, monday, provider)
            if do_global:
                WeeklyActivity.recalculate(None, monday, provider)

    def __str__(self):
        label = self.project_id or "global"
        return f"{label}[{self.provider}] / {self.date} = {self.user_message_count}"


class WeeklyActivity(PeriodicActivity):
    """Pre-computed weekly activity metrics (messages, sessions, costs), per project and global."""

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Weekly range: Monday 00:00 UTC to next Monday 00:00 UTC."""
        date_start = datetime.combine(activity_date, datetime.min.time(), tzinfo=timezone.utc)
        return date_start, date_start + timedelta(days=7)


class DailyActivity(PeriodicActivity):
    """Pre-computed daily activity metrics (messages, sessions, costs), per project and global."""

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Daily range: day 00:00 UTC to next day 00:00 UTC."""
        date_start = datetime.combine(activity_date, datetime.min.time(), tzinfo=timezone.utc)
        return date_start, date_start + timedelta(days=1)


class Session(models.Model):
    """A session corresponds to a *.jsonl file, or a subagent file in {session_id}/subagents/"""

    id = models.CharField(max_length=255, primary_key=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    provider = models.CharField(max_length=50, db_index=True)  # Backend provider (see Provider enum)
    # Provider-relative path to the JSONL file (relative to the provider's data root,
    # e.g. ~/.claude/projects/ for claude_code, ~/.codex/sessions/ for codex). The file
    # path is stored explicitly because it cannot always be derived from the session id
    # alone (e.g. codex files are organised by date, not by project). Unique because
    # one JSONL file maps to exactly one session — the unique constraint also covers
    # the path-based lookups done by initial sync and the file watcher.
    file_path = models.CharField(max_length=500, unique=True)
    last_offset = models.PositiveBigIntegerField(default=0)
    last_line = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    stale = models.BooleanField(default=False)  # True if folder/file no longer exists on disk
    compute_version = models.PositiveIntegerField(null=True, blank=True)  # NULL = never computed
    search_version = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=250, null=True, blank=True)  # Session title (from first user message or custom-title)
    user_message_count = models.PositiveIntegerField(default=0)  # Number of user messages (message turns)

    # Cost and context usage fields (computed from items)
    context_usage = models.PositiveIntegerField(null=True, blank=True)  # Current context usage (last known value in tokens)
    self_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Own items cost in USD
    subagents_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Sum of subagents total_cost
    total_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # self_cost + subagents_cost

    # Subagent-related fields
    type = models.CharField(
        max_length=20,
        choices=SessionType.choices,
        default=SessionType.SESSION,
        db_index=True,
    )
    parent_session = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subagents',
    )
    agent_id = models.CharField(max_length=255, null=True, blank=True)  # Short agent ID from filename (e.g., "a6c7d21")

    # Runtime environment fields (last known values from JSONL)
    cwd = models.CharField(max_length=500, null=True, blank=True)  # Current working directory
    cwd_git_branch = models.CharField(max_length=255, null=True, blank=True)  # Git branch from cwd (unreliable for worktrees)
    model = models.CharField(max_length=100, null=True, blank=True)  # Last model identifier seen on the session (provider-specific format, e.g. "claude-opus-4-5-20251101" for Claude Code)
    slug = models.CharField(max_length=255, null=True, blank=True)  # Last session slug (e.g., "gleaming-marinating-twilight")

    # Session creation timestamp (from first JSONL item with a timestamp)
    created_at = models.DateTimeField(null=True, blank=True)

    # Resolved git directory and branch (from filesystem analysis of tool_use paths)
    git_directory = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Resolved branch name (or commit hash for detached HEAD)

    # Lifecycle timestamps (datetime, unlike mtime which is a raw float for the JSONL file)
    last_started_at = models.DateTimeField(null=True, blank=True)  # Last time the session was started or resumed
    last_updated_at = models.DateTimeField(null=True, blank=True)  # Last time meaningful content was added
    last_stopped_at = models.DateTimeField(null=True, blank=True)  # Last time the session process stopped (our processes only)

    # Content tracking (for "unread" detection)
    last_new_content_at = models.DateTimeField(null=True, blank=True)  # Last time an assistant message was synced
    last_viewed_at = models.DateTimeField(null=True, blank=True)  # Last time the user viewed this session

    # User-controlled fields
    archived = models.BooleanField(default=False)  # User can archive sessions to hide them from default list
    # User can pin sessions: NULL = not pinned, string = pin mode (scope/workspace/always).
    # Any truthy value means the session is pinned; mode controls cross-filter visibility.
    pinned = models.CharField(max_length=16, choices=PinMode.choices, null=True, blank=True, default=None)
    # Hidden sessions are top-level (type=SESSION) sessions invisible to
    # the user — they exist, consume API costs (counted in aggregates),
    # but are absent from every list, search result, count, and broadcast.
    # Set by the CLI `twicc create-session --hidden`; mutable through
    # `twicc update-session <ID> hide / unhide`. Implies a non-interactive
    # permission_mode (bypassPermissions/dontAsk for Claude Code; yolo/strict
    # for Codex) and question_widget=False — both enforced at create / flip
    # time by `validate_hidden_constraints`. Subagent sessions
    # (`type=SUBAGENT`) ignore this flag entirely: they are already invisible
    # everywhere via the `type=SESSION` filter.
    hidden = models.BooleanField(default=False, db_index=True)
    # Trace of the session that invoked the CLI to create this one
    # (filiation). Set automatically by `twicc create-session` via PID
    # ancestry (cf. `twicc whoami`); not exposed as a CLI flag. Independent
    # of `hidden`: a visible session can also have spawned_by set. Immutable
    # after creation (no UI / CLI mutates it). `on_delete=SET_NULL` so child
    # sessions survive deletion of the parent (no cascade).
    spawned_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
        related_name="spawned_sessions",
        db_index=True,
    )
    # Root session for this spawned-session tree. Set on sessions created via
    # ``twicc create-session`` from another session; ordinary UI/manual sessions
    # keep it null until they spawn their first child, then point to themselves.
    # Denormalized to fetch an orchestration tree in one query while
    # ``spawned_by`` remains the direct parent edge.
    spawn_root = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
        related_name="spawn_tree_sessions",
        db_index=True,
    )
    # Free-form agent-facing data attached at session creation time. The core
    # does not interpret it and the UI does not expose editing controls for it.
    annotations = models.JSONField(default=dict, blank=True)
    # TwiCC system-prompt addendum frozen at session creation time. Composed by
    # ``core.services.session_creation`` and stashed via
    # ``pending_session_attributes``; the watcher copies it here when it
    # creates the row from the first JSONL line. NEVER mutated after creation:
    # the Anthropic / OpenAI prompt cache requires byte-for-byte stability of
    # the system prompt across every turn of the session, including across
    # resumes that follow a TwiCC restart. ``twicc-whoami`` is the canonical
    # source for the *live* values that this snapshot froze. NULL for sessions
    # created before this column existed; the agent code passes the SDK no
    # addendum in that case (preserving their cache prefix).
    system_prompt_addendum = models.TextField(null=True, blank=True, default=None)
    # Hybrid CLI mode: when True, this session is driven by the interactive
    # Claude Code CLI running in a dedicated tmux session instead of the SDK.
    # One-way: once a session has been resumed by the CLI it can never go back
    # to the SDK (the SDK no longer sees CLI-era messages), so this flag is
    # never reset to False. Human-only: only settable from the web UI (WS),
    # never from the agent-facing CLI or drop-request paths.
    hybrid = models.BooleanField(default=False)

    # Per-session permission mode. Values are provider-specific (e.g. "default",
    # "acceptEdits", "plan", "bypassPermissions" for Claude Code). NULL = use global default.
    permission_mode = models.CharField(max_length=30, null=True, default=None)
    # Per-session selected model identifier. Values are provider-specific
    # (e.g. "opus" / "sonnet" / "haiku" for Claude Code). NULL = use global default.
    selected_model = models.CharField(max_length=30, null=True, default=None)
    # Effort level for thinking depth (e.g., "low", "medium", "high", "max")
    effort = models.CharField(max_length=10, null=True, default=None)
    # Whether extended thinking is enabled (True=adaptive, False=disabled)
    thinking_enabled = models.BooleanField(null=True, default=None)
    # Whether the built-in Chrome MCP (Claude in Chrome) is activated for this session.
    # NULL = use global default, explicit value = forced for this session.
    #
    # Deliberately Claude Code-specific by name and meaning, but kept on the
    # generic ``Session`` row alongside the other ``AgentSettings`` fields.
    # The ``AgentSettings`` NamedTuple is the closed bundle every provider
    # receives via :meth:`BaseProviderHelpers.resolve_agent_settings`; each
    # provider ignores the fields it doesn't use. Splitting this column off
    # into a per-provider side table — or generalising the name —
    # would ripple through serializers, the WS payload, the synced settings
    # mapping, and the front store, which is not worth the churn for a
    # single flag. New provider-specific session-level flags should follow
    # the same pattern: add a column here, classify it in the owning
    # provider's ``AGENT_SETTINGS_CATEGORIES`` / ``AGENT_SETTINGS_FIELDS_MAPPING``,
    # and let the others ignore it.
    claude_in_chrome = models.BooleanField(null=True, default=None)
    # Whether Claude Code's fast mode is enabled for this session. NULL = use
    # global default, explicit value = forced for this session. Only valid on
    # supported Opus models; the helpers clamp it to ``False`` for any other
    # model. Claude Code-specific like ``claude_in_chrome``; see the comment
    # above for the closed-bundle rationale.
    fast_mode = models.BooleanField(null=True, default=None)
    # Maximum context window size in tokens (200_000 = default 200K, 1_000_000 = extended 1M)
    # NULL = use global default, explicit value = forced for this session
    context_max = models.PositiveIntegerField(null=True, default=None)
    # Whether interactive question widgets (e.g. AskUserQuestion for Claude
    # Code) are enabled for this session. When True (or NULL = use default),
    # the agent may use those UI tools to prompt the user; when False, the
    # corresponding tool names are added to ``disallowed_tools`` at process
    # start, forcing the agent to ask questions as plain text instead.
    # Useful for CLI workflows where the user wants to read and answer
    # questions textually without going through the TwiCC UI.
    #
    # Intentionally generic by name (not tied to a specific provider tool)
    # so any future provider with a similar widget can opt in by mapping
    # ``question_widget=False`` to its own widget tool. Hidden from the
    # frontend via the global ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` set;
    # settable only via the CLI flag ``--no-question-widget``. See the
    # ``claude_in_chrome`` comment above for the closed-bundle rationale.
    question_widget = models.BooleanField(null=True, default=None)

    # Whether the session's context has been compacted at least once
    compacted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-mtime"]
        indexes = [
            # Covers all "list sessions by project+type" queries (listing + count + sync)
            models.Index(fields=["project", "type", "-mtime"], name="idx_session_project_type_mtime"),
            # Covers API session listing and project sessions_count (most frequent queries)
            models.Index(
                fields=["project", "-mtime"],
                name="idx_session_visible",
                condition=models.Q(
                    user_message_count__gt=0,
                    type="session",
                    created_at__isnull=False,
                    hidden=False,
                ),
            ),
        ]

    @property
    def cutoff(self) -> datetime | None:
        """The most recent lifecycle boundary: max of last_started_at and last_stopped_at."""
        if self.last_stopped_at is None:
            return self.last_started_at
        if self.last_started_at is None:
            return self.last_stopped_at
        return max(self.last_started_at, self.last_stopped_at)

    def recalculate_costs(self) -> None:
        """Recalculate self_cost, subagents_cost, and total_cost from SessionItem costs.

        - self_cost = Sum of this session's own SessionItem.cost
        - subagents_cost = Sum of SessionItem.cost for all subagent sessions
        - total_cost = self_cost + subagents_cost
        """
        from decimal import Decimal
        from django.db.models import Sum

        self.self_cost = SessionItem.objects.filter(
            session=self,
            cost__isnull=False,
        ).aggregate(total=Sum("cost"))["total"]

        self.subagents_cost = SessionItem.objects.filter(
            session__parent_session=self,
            cost__isnull=False,
        ).aggregate(total=Sum("cost"))["total"]

        total = (self.self_cost or Decimal(0)) + (self.subagents_cost or Decimal(0))
        self.total_cost = total if total > 0 else None

    def __str__(self):
        return self.id


class ArtifactBookmark(models.Model):
    """A user-saved pointer to one rendered artifact file, with a display name
    and a visibility scope. Scope reuses PinMode and mirrors session pinning:
    'project' stays local, 'workspace' surfaces in workspace views, 'all' in the
    global view. "Not bookmarked" = no row."""

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="artifact_bookmarks",
    )
    # Denormalised from session.project so scope filtering needs no join.
    # Intentionally the RAW project: for a worktree session this is the worktree
    # project, NOT the main repo. The worktree -> main-repo mapping happens at
    # scope-resolution time on the frontend (see the design doc, §5).
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="artifact_bookmarks",
    )
    # Path relative to the session's artifacts dir, e.g. "demo/index.html".
    relative_path = models.TextField()
    name = models.CharField(max_length=255)
    scope = models.CharField(max_length=16, choices=PinMode.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "relative_path"],
                name="uniq_artifact_bookmark_session_path",
            ),
        ]
        indexes = [
            models.Index(fields=["project"], name="idx_artifactbookmark_project"),
        ]


class SessionItem(models.Model):
    """A session item corresponds to a single line in a JSONL file"""

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="items",
    )
    line_num = models.PositiveIntegerField()  # 1-based (first line = 1)
    content = models.TextField()

    # Display metadata fields
    display_level = models.PositiveSmallIntegerField(null=True, blank=True)  # ItemDisplayLevel enum value
    group_head = models.PositiveIntegerField(null=True, blank=True, db_index=True)  # line_num of group start
    group_tail = models.PositiveIntegerField(null=True, blank=True)  # line_num of group end
    kind = models.CharField(max_length=50, null=True, blank=True)  # Item category/type

    # Cost and usage fields (from API response)
    message_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)  # API message ID for deduplication
    cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Line cost in USD (null if no usage or duplicate message_id)
    context_usage = models.PositiveIntegerField(null=True, blank=True)  # Total tokens at this point (null if no usage)

    # Timestamp from JSONL line (stored as datetime in UTC)
    timestamp = models.DateTimeField(null=True, blank=True)

    # Resolved git directory and branch (from filesystem analysis of tool_use paths)
    git_directory = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Resolved branch name (or commit hash for detached HEAD)

    class Meta:
        ordering = ["line_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "line_num"],
                name="unique_session_line",
            )
        ]
        indexes = [
            models.Index(
                fields=["session", "kind", "line_num"],
                name="idx_session_kind_line",
            ),
            # used to recompute activity
            models.Index(
                fields=["session", "timestamp"],
                name="idx_item_user_session",
                condition=Q(kind=ItemKind.USER_MESSAGE.value)
            ),
            models.Index(
                fields=["timestamp"],
                name="idx_item_user_all",
                condition=Q(kind=ItemKind.USER_MESSAGE.value)
            ),
            models.Index(
                fields=["session", "timestamp"],
                name="idx_item_cost_session",
                condition=Q(cost__isnull=False)
            ),
            models.Index(
                fields=["timestamp"],
                name="idx_item_cost_all",
                condition=Q(cost__isnull=False)
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.line_num}"


class ToolResultLink(models.Model):
    """Links one tool_use to one tool_result within a session.

    App-centric and provider-agnostic: every backend that exposes tool
    calls (Claude Code, future Codex, …) is expected to populate this
    table from its own JSONL parsing as part of building
    :class:`SessionItem` rows. The table is not scoped by ``provider``
    directly — the parent :class:`Session` already carries that scope,
    and the join enforces it. Concrete ``tool_name`` values (``Bash``,
    ``Edit``, ``apply_patch``, …) are whatever each provider emits;
    ``extra`` carries provider-specific structured payloads (e.g. diff
    stats for Edit-style tools).
    """

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="tool_result_links",
    )
    tool_use_line_num = models.PositiveIntegerField()  # Line containing the tool_use
    tool_result_line_num = models.PositiveIntegerField()  # Line containing the tool_result
    tool_use_id = models.CharField(max_length=255)  # The tool_use ID
    tool_name = models.CharField(max_length=255, default="")  # Provider-emitted tool name (free-form)
    tool_result_at = models.DateTimeField(null=True, blank=True)  # Timestamp of the tool_result item
    extra = models.TextField(null=True, blank=True)  # Optional provider-specific extra data
    error = models.TextField(null=True, blank=True)  # Error message from tool_result (None = no error)

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_line_num", "tool_use_id"],
                name="idx_tool_result_link_lookup",
            ),
            models.Index(
                fields=["session", "tool_name"],
                name="idx_tool_result_link_by_name",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.tool_use_line_num}->{self.tool_result_line_num} ({self.tool_use_id})"


class AgentLink(models.Model):
    """Links a tool_use that spawns a subagent to that subagent's session.

    App-centric and provider-agnostic: any provider whose CLI exposes a
    "spawn an agent" tool (Claude Code's ``Task``, a hypothetical
    Codex ``delegate``, …) is expected to populate this table as it
    parses its JSONL into :class:`SessionItem` rows. The table is not
    scoped by ``provider`` directly — the parent :class:`Session`
    already carries that scope, and the join enforces it. The shape
    fits any spawn-an-agent tool: ``tool_use_id`` identifies the call,
    ``agent_id`` the spawned session, ``is_background`` whether the
    parent waits for the child synchronously or not.
    """

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="agent_links",
    )
    tool_use_line_num = models.PositiveIntegerField()  # Line containing the assistant message with the spawning tool_use
    tool_use_id = models.CharField(max_length=255)  # ID of the spawning tool_use
    agent_id = models.CharField(max_length=255)  # The subagent ID
    is_background = models.BooleanField(default=False)  # True when the parent does not wait synchronously
    started_at = models.DateTimeField(null=True, blank=True)  # Timestamp of the spawning tool_use item

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_id"],
                name="idx_agent_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.tool_use_line_num} -> agent {self.agent_id} ({self.tool_use_id})"


class ModelPrice(models.Model):
    """Historical pricing for AI models, scoped per backend ``provider``.

    Each row is a snapshot of a single model's prices for a given
    ``effective_date`` — a new row is only inserted when the prices
    change, so historical lookups can find the right rate for an old
    message. The ``provider`` field bounds the namespace for ``model_id``
    so that two providers with overlapping naming (unlikely for
    OpenRouter today, but possible) do not collide.
    """

    # Backend provider that produced this price row (see Provider enum)
    provider = models.CharField(max_length=50)

    # OpenRouter model ID (e.g. "anthropic/claude-opus-4.5", "openai/gpt-5.4-mini")
    model_id = models.CharField(max_length=100)

    # Date from which this price is effective
    effective_date = models.DateField()

    # Prices in USD per million tokens
    input_price = models.DecimalField(max_digits=12, decimal_places=6)
    output_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_read_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_write_5m_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_write_1h_price = models.DecimalField(max_digits=12, decimal_places=6)

    # When this record was created
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # The UniqueConstraint's implicit B-tree index on
            # ``(provider, model_id, effective_date ASC)`` is enough to
            # serve every read pattern we have here: SQLite reverse-scans
            # it for ``ORDER BY -effective_date LIMIT 1`` and range-scans
            # it for ``effective_date <= target_date`` lookups, and the
            # table is small (one row per price change). No separate
            # ``-effective_date`` index needed.
            models.UniqueConstraint(
                fields=["provider", "model_id", "effective_date"],
                name="uniq_mp_prov_mid_date",
            ),
        ]

    def __str__(self):
        return f"[{self.provider}] {self.model_id} ({self.effective_date})"

    # ── In-memory price cache ──────────────────────────────────────────
    # Lazily populated on first access, invalidated by invalidate_price_cache().
    # Nested by provider so each provider's family fallback stays scoped to its
    # own model_ids (no cross-provider bleed in the same-family search).

    _prices_by_model: ClassVar[dict[str, dict[str, list["ModelPrice"]]] | None] = None
    _models_by_family: ClassVar[dict[str, dict[str, list[str]]] | None] = None

    @classmethod
    def _ensure_price_cache(cls) -> None:
        """Load every :class:`ModelPrice` row into memory, indexed per provider."""
        if cls._prices_by_model is not None:
            return

        from collections import defaultdict

        from twicc.providers.helpers import get_provider_helpers_registry

        prices_by_model: dict[str, dict[str, list[ModelPrice]]] = defaultdict(
            lambda: defaultdict(list),
        )
        models_by_family: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list),
        )

        for price in cls.objects.all().order_by("provider", "model_id", "-effective_date"):
            prices_by_model[price.provider][price.model_id].append(price)

        registry = get_provider_helpers_registry()
        for provider_value, model_map in prices_by_model.items():
            try:
                helpers = registry.get(Provider(provider_value))
            except (KeyError, ValueError):
                continue
            for model_id in model_map:
                family, _ = helpers.extract_family_and_version(model_id)
                if family:
                    models_by_family[provider_value][family].append(model_id)

        cls._prices_by_model = {
            provider_value: dict(model_map)
            for provider_value, model_map in prices_by_model.items()
        }
        cls._models_by_family = {
            provider_value: dict(family_map)
            for provider_value, family_map in models_by_family.items()
        }

    @classmethod
    def invalidate_price_cache(cls) -> None:
        """Reset the in-memory price cache. Call after price data is updated."""
        cls._prices_by_model = None
        cls._models_by_family = None

    @classmethod
    def get_price_for_date(
        cls, provider: Provider, model_id: str, target_date: date,
    ) -> "ModelPrice | None":
        """Resolve the applicable price for ``(provider, model_id)`` at ``target_date``.

        Uses an in-memory cache (loaded lazily on first call) to avoid
        an SQL query on every invocation. The cache is invalidated via
        :meth:`invalidate_price_cache` when price data is updated.

        Fallback chain (each step scoped to the same ``provider``):

        1. exact ``model_id`` row with ``effective_date <= target_date``;
        2. exact ``model_id`` oldest known row (for messages older than
           any tracked price);
        3. same family, lower version (closest first);
        4. same family, higher version (closest first);
        5. ``None`` — caller should fall back to the helper's
           :attr:`DEFAULT_FAMILY_PRICES`.
        """
        cls._ensure_price_cache()
        assert cls._prices_by_model is not None
        assert cls._models_by_family is not None

        provider_value = provider.value
        prices_by_model = cls._prices_by_model.get(provider_value, {})

        # 1. Try to find price valid at target_date for exact model
        model_prices = prices_by_model.get(model_id)
        if model_prices:
            # List is sorted by effective_date descending
            for price in model_prices:
                if price.effective_date <= target_date:
                    return price
            # 2. Fallback: oldest known price for exact model (last in descending list)
            return model_prices[-1]

        # 3 & 4. Try other versions of the same family
        from twicc.providers.helpers import get_provider_helpers

        helpers = get_provider_helpers(provider)
        family, version = helpers.extract_family_and_version(model_id)
        if not family:
            return None

        family_model_ids = cls._models_by_family.get(provider_value, {}).get(family)
        if not family_model_ids:
            return None

        def extract_version_tuple(mid: str) -> tuple[int, ...]:
            """Extract a sortable version tuple from a sibling ``model_id``."""
            _, v = helpers.extract_family_and_version(mid)
            if not v:
                return (0,)
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        if version:
            try:
                target_version = tuple(int(x) for x in version.split("."))
            except ValueError:
                target_version = (0,)

            lower_versions = [
                m for m in family_model_ids if extract_version_tuple(m) < target_version
            ]
            higher_versions = [
                m for m in family_model_ids if extract_version_tuple(m) > target_version
            ]

            lower_versions.sort(key=extract_version_tuple, reverse=True)
            higher_versions.sort(key=extract_version_tuple)

            for fallback_model_id in lower_versions + higher_versions:
                fallback_prices = prices_by_model.get(fallback_model_id)
                if fallback_prices:
                    # Return most recent price for the fallback model
                    return fallback_prices[0]

        # 5. No price found at all
        return None


class UsageSnapshot(models.Model):
    """
    A point-in-time snapshot of a provider's usage quotas.

    Each row represents one fetch from the provider's usage source
    (e.g. the Anthropic OAuth usage API for Claude Code), storing both
    parsed fields and the raw response for investigation purposes.
    """

    # Backend provider that produced this snapshot (see Provider enum)
    provider = models.CharField(max_length=50)

    # When the API was called
    fetched_at = models.DateTimeField()

    # Raw JSON response for future-proofing and investigation
    raw_response = models.JSONField()

    # Five-hour rolling window quota (null if not yet used in this window)
    five_hour_utilization = models.FloatField(null=True, blank=True)
    five_hour_resets_at = models.DateTimeField(null=True, blank=True)

    # Seven-day rolling window quota - global (null if not yet used in this window)
    seven_day_utilization = models.FloatField(null=True, blank=True)
    seven_day_resets_at = models.DateTimeField(null=True, blank=True)

    # Extra usage (default False if the block is absent).
    #
    # Two display modes coexist on the same columns, selected by which fields
    # the producing provider populates:
    #
    # - Claude Code (Anthropic) reports a "credits used over a monthly limit"
    #   block: ``monthly_limit`` + ``used_credits`` + ``utilization`` (0-100)
    #   are set, ``remaining_credits`` stays null. The UI renders a percent ring.
    #
    # - Codex (ChatGPT) only reports a remaining balance with no enclosing
    #   limit, so ``remaining_credits`` is set and the three Anthropic-shape
    #   fields stay null. The UI renders an absolute "X credits" counter.
    extra_usage_is_enabled = models.BooleanField(default=False)
    extra_usage_monthly_limit = models.IntegerField(null=True, blank=True)
    extra_usage_used_credits = models.FloatField(null=True, blank=True)
    extra_usage_utilization = models.FloatField(null=True, blank=True)
    extra_usage_remaining_credits = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        indexes = [
            models.Index(
                fields=["provider", "-fetched_at"],
                name="idx_usage_snap_prov_fetch",
            ),
        ]

    def __str__(self):
        return f"UsageSnapshot[{self.provider}] {self.fetched_at.isoformat()}"

    # --- Computed properties ---

    @staticmethod
    def _temporal_pct(fetched_at: datetime, resets_at: datetime, window: timedelta) -> float:
        """Calculate how far through a time window we are, as a percentage (0-100)."""
        window_start = resets_at - window
        elapsed = (fetched_at - window_start).total_seconds()
        total = window.total_seconds()
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, (elapsed / total) * 100.0))

    @property
    def five_hour_temporal_pct(self) -> float | None:
        if self.five_hour_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.five_hour_resets_at, timedelta(hours=5))

    @property
    def seven_day_temporal_pct(self) -> float | None:
        if self.seven_day_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.seven_day_resets_at, timedelta(days=7))

    @staticmethod
    def _burn_rate(utilization: float | None, temporal_pct: float | None) -> float | None:
        """
        Ratio of utilization to temporal progress.

        > 1.0 means on track to exhaust quota before reset.
        """
        if utilization is None or temporal_pct is None or temporal_pct <= 0:
            return None
        return utilization / temporal_pct

    @property
    def five_hour_burn_rate(self) -> float | None:
        return self._burn_rate(self.five_hour_utilization, self.five_hour_temporal_pct)

    @property
    def seven_day_burn_rate(self) -> float | None:
        return self._burn_rate(self.seven_day_utilization, self.seven_day_temporal_pct)

    @property
    def five_hour_started_at(self) -> datetime | None:
        if self.five_hour_resets_at is None:
            return None
        return self.five_hour_resets_at - timedelta(hours=5)

    @property
    def seven_day_started_at(self) -> datetime | None:
        if self.seven_day_resets_at is None:
            return None
        return self.seven_day_resets_at - timedelta(days=7)


class ProcessRun(models.Model):
    """Persistent record of an agent run, used to surface live/recent state.

    A row is created when an agent is registered with its manager and updated
    on every state transition (:attr:`state` + :attr:`last_state_change_at`).
    Rows with a non-``DEAD`` :attr:`state` represent an agent the in-memory
    registry currently considers alive; ``DEAD`` rows survive only when a
    provider explicitly chooses to keep them post-mortem (see
    ``BaseProviderHelpers.should_keep_dead_process_run``) — e.g. Claude Code
    keeps DEAD rows that still have :class:`SessionCron` rows attached so the
    boot-time cron restart can pick them up.

    The :attr:`state` column reuses :class:`twicc.agent.states.AgentState`'s
    values directly (no DB-side duplicate enum); see ``_AGENT_STATE_CHOICES``
    above for the derived ``choices`` list.

    Uses a plain CharField for session_id (not a FK) because new sessions may
    not exist in the Session table yet when the process starts — the Session
    row is created asynchronously by the file watcher when the JSONL file
    appears.
    """

    provider = models.CharField(max_length=50)  # Backend provider (see Provider enum)
    session_id = models.CharField(max_length=200)
    started_at = models.DateTimeField()
    state = models.CharField(
        max_length=20,
        choices=_AGENT_STATE_CHOICES,
        default=AgentState.STARTING.value,
    )
    last_state_change_at = models.DateTimeField()
    # OS PIDs captured at row creation, for external introspection. Both are
    # nullable: ``twicc_pid`` is unknown for rows imported from older schemas;
    # ``agent_pid`` is also unset when the provider's subprocess has not been
    # started yet (the typical state at ``ProcessRun.objects.create`` time —
    # ``agent.start()`` runs right after). No write site updates either field
    # past creation today; external tooling that needs to match a PID against
    # the live TwiCC instance reads ``twicc.info.json`` to find the current
    # process and filters rows accordingly.
    twicc_pid = models.IntegerField(null=True, blank=True)
    agent_pid = models.IntegerField(null=True, blank=True)
    # ``True`` while the agent is blocked on a user click (tool approval,
    # AskUserQuestion, Codex approval). Orthogonal to ``state`` because the
    # runtime stays in ``ASSISTANT_TURN`` while waiting: the SDK's
    # ``can_use_tool`` callback simply blocks until the user resolves the
    # request, so an external observer reading just ``state`` cannot tell
    # "currently generating" from "blocked on user click". This column
    # disambiguates. Maintained by :meth:`BaseAgentManager._persist_process_run_transition`
    # via ``bool(agent.pending_requests)``, forced to ``False`` on ``DEAD``.
    awaiting_user_input = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["session_id"], name="idx_processrun_session"),
        ]

    def __str__(self):
        return f"ProcessRun[{self.provider}] {self.pk} for session {self.session_id}"


class SessionCron(models.Model):
    """Persisted cron job created by a Claude Code session via the CLI's cron tool.

    Today this table is **Claude Code-specific**: every callsite outside
    of :mod:`twicc.asgi` lives in :mod:`twicc.providers.claude_code`,
    and every field on the row encodes a Claude CLI behaviour:

    - ``CronCreate`` / ``CronDelete`` are the Claude SDK PostToolUse
      hooks that drive insert/delete (see
      ``providers/claude_code/agent/manager.py``).
    - The auto-expiry rule, the jitter model, and the
      :attr:`CLAUDE_RECURRING_MAX_AGE` ceiling all match the Claude CLI
      semantics — they are *not* generic cron semantics.
    - The ``last_fire`` / ``expired_at`` cached properties exist to
      mirror the CLI's own auto-deletion timer.

    The :attr:`provider` column is kept (defaulting to
    :attr:`Provider.CLAUDE_CODE`) so that a future provider exposing an
    equivalent surface can reuse the table — but it would need to share
    the same expiry semantics or replace them via a per-provider hook.
    Until that happens, treat any addition here as Claude Code-only.

    Uses a plain CharField for ``session_id`` (not a FK) for the same
    reason as :class:`ProcessRun`: new sessions may not exist in the
    :class:`Session` table yet when a cron row is created.
    """

    CLAUDE_RECURRING_MAX_AGE = timedelta(days=7)
    """Recurring crons auto-delete after this delay — matches the Claude CLI's own auto-delete window."""

    provider = models.CharField(max_length=50)  # Backend provider (see Provider enum)
    cron_id = models.CharField(max_length=100, unique=True)
    session_id = models.CharField(max_length=200)
    process_run = models.ForeignKey(ProcessRun, on_delete=models.CASCADE, related_name="crons", null=True, blank=True)
    cron_expr = models.CharField(max_length=100)
    recurring = models.BooleanField()
    prompt = models.TextField()
    created_at = models.DateTimeField()
    next_fire = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["session_id"], name="idx_sessioncron_session"),
        ]

    def __str__(self):
        kind = "recurring" if self.recurring else "one-shot"
        return f"Cron[{self.provider}] {self.cron_id} ({kind}) on session {self.session_id}"

    def serialize(self) -> dict:
        """Serialize for WebSocket transmission (matches the format expected by the frontend)."""
        return {
            "id": self.cron_id,
            "cron_expr": self.cron_expr,
            "recurring": self.recurring,
            "prompt": self.prompt,
            "created_at": self.created_at.timestamp(),
            "next_fire": self.next_fire.timestamp(),
        }

    JITTER_SAFETY_MARGIN = timedelta(minutes=1)
    """Margin added after the CLI's jitter to ensure the last fire has completed (Claude CLI-specific)."""

    @cached_property
    def last_fire(self) -> datetime | None:
        """Timestamp at which the Claude CLI will fire this cron for the last time before auto-deleting it.

        Only meaningful for recurring crons. Returns the first cron
        occurrence that falls at or after
        ``created_at + CLAUDE_RECURRING_MAX_AGE`` (7 days). This is the
        fire that triggers the CLI's age check and causes deletion.
        Tied to Claude CLI behaviour — another provider would need its
        own equivalent.

        Returns ``None`` for non-recurring crons or if the cron
        expression is unparseable.
        """
        if not self.recurring:
            return None
        deadline = self.created_at + self.CLAUDE_RECURRING_MAX_AGE
        try:
            for occurrence in cron_occurrences(self.cron_expr, self.created_at):
                if occurrence >= deadline:
                    return occurrence
        except Exception:
            pass
        return None

    @cached_property
    def expired_at(self) -> datetime | None:
        """Timestamp after which this recurring cron has certainly finished its last fire.

        Computed as: ``last_fire + jitter_max + safety_margin``. The
        Claude CLI adds a random jitter of 10% of the cron period
        (capped at 15 minutes) to each fire; we add
        :attr:`JITTER_SAFETY_MARGIN` on top. Tied to Claude CLI
        behaviour.

        Returns ``None`` for non-recurring crons or if
        :attr:`last_fire` cannot be computed.
        """
        if self.last_fire is None:
            return None
        # Compute period from two consecutive occurrences
        try:
            it = cron_occurrences(self.cron_expr, self.created_at)
            first = next(it)
            second = next(it)
            period = second - first
        except Exception:
            # Can't compute period — use max jitter (15 min) as fallback
            period = timedelta(hours=3)  # 10% of 3h = 18min > 15min cap → will use cap
        jitter_max = min(period * 0.1, timedelta(minutes=15))
        return self.last_fire + jitter_max + self.JITTER_SAFETY_MARGIN

    @classmethod
    def has_active_for_session(cls, session_id: str, provider: Provider) -> bool:
        """Check if ``session_id`` has any non-expired cron jobs for ``provider``.

        Recurring crons expire after CLAUDE_RECURRING_MAX_AGE (7 days, matching CLI behavior).
        One-shot crons expire after their fire time passes.
        """
        now = datetime.now(tz=timezone.utc)
        return cls.objects.filter(
            session_id=session_id,
            provider=provider.value,
        ).filter(
            # Recurring: created less than 7 days ago
            Q(recurring=True, created_at__gt=now - cls.CLAUDE_RECURRING_MAX_AGE)
            # One-shot: fire time not yet passed
            | Q(recurring=False, next_fire__gt=now)
        ).exists()

    @classmethod
    def active_for_session(cls, session_id: str, provider: Provider) -> models.QuerySet:
        """Return the queryset of non-expired crons for ``(session_id, provider)``."""
        now = datetime.now(tz=timezone.utc)
        return cls.objects.filter(
            session_id=session_id,
            provider=provider.value,
        ).filter(
            Q(recurring=True, created_at__gt=now - cls.CLAUDE_RECURRING_MAX_AGE)
            | Q(recurring=False, next_fire__gt=now)
        )


class Command(models.Model):
    """One command discovered for a given backend provider.

    Each provider runs its own discovery / sync task — typically scanning
    user-level, project-level, and plugin sources on disk — and writes
    its findings here. Rows scope to ``provider`` so two providers can
    expose the same ``name`` without colliding, and so each provider's
    task only touches its own subset.

    The ``activation_char`` field captures the prefix the user types to
    invoke the command (e.g. ``/`` for Claude Code; Codex supports both
    ``/`` and ``$``).

    Commands with ``project=None`` are global (available for all projects
    of that provider).
    """

    provider = models.CharField(max_length=50)  # Backend provider (see Provider enum)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="commands",
        null=True,
        blank=True,  # NULL = global (available for all projects)
    )
    name = models.CharField(max_length=200)  # e.g. "commit", "superpowers:brainstorming"
    activation_char = models.CharField(max_length=1)  # Prefix character used to invoke the command (e.g. "/" or "$")
    plugin_name = models.CharField(max_length=100, null=True, blank=True)  # e.g. "superpowers" when shipped by a plugin
    description = models.TextField()
    argument_hint = models.CharField(max_length=200, null=True, blank=True)  # e.g. "[review-aspects]"
    # Whether this row is a built-in command shipped by the provider's
    # runtime (e.g. Codex's ``System`` / ``Admin`` skills). Drives the
    # ``(built-in)`` tag in the picker. Claude Code's built-ins still
    # live in a frontend constant and never reach this table, so every
    # existing Claude row stays at the default ``False``.
    is_builtin = models.BooleanField(default=False)

    class Meta:
        indexes = [
            # Leftmost-prefix on ``provider`` so the per-provider sync
            # task hits it on its own, and the API lookup also hits the
            # full triple ``(provider, activation_char, project)``.
            models.Index(fields=["provider", "activation_char", "project"], name="idx_command_prov_act_proj"),
        ]

    def __str__(self):
        scope = self.project_id or "global"
        return f"[{self.provider}] {self.activation_char}{self.name} ({scope})"
