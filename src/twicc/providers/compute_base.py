"""
Provider-agnostic compute primitives for session items.

Each provider stores its native JSONL lines in :class:`~twicc.core.models.SessionItem.content`
without conversion at ingestion time. This module owns the shared *output*
structures every provider produces from those lines (kind, display level,
group membership, agent / tool-result link records) and the machinery that
operates on those outputs (group state machine, agent prompt cache, batch
orchestration, watcher live sync).

The :class:`BaseSessionCompute` class is the compute surface every provider
inherits. Each provider lives under ``providers/<name>/compute.py`` and
overrides the extraction methods (``compute_item_kind``,
``extract_tool_result_info``, ...) by parsing its own native format.
Higher-level orchestration methods (group state, link creation, batch
compute, watcher sync) are concrete in this base class — they are
implemented in terms of the abstract extractors so each provider gets
them for free once its parsing layer is in place.

Unlike :class:`~twicc.providers.helpers.BaseProviderHelpers`, there is no
cross-provider registry for the compute classes: each provider's
orchestrator instantiates and uses its own subclass directly. The compute
surface is internal plumbing — nothing outside a provider's ingestion
path needs to dispatch dynamically by ``Provider`` enum.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from twicc.core.enums import ItemDisplayLevel, ItemKind, Provider

if TYPE_CHECKING:
    from pathlib import Path

    from twicc.core.models import Session, SessionItem


# =============================================================================
# Shared NamedTuples — broadcast updates and extraction outputs
# =============================================================================


class AgentLinkUpdate(NamedTuple):
    """Describes a new AgentLink creation to broadcast to the frontend."""
    parent_session_id: str
    agent_id: str
    tool_use_id: str
    tool_use_line_num: int
    is_background: bool
    started_at: datetime | None


class ToolResultUpdate(NamedTuple):
    """Describes a tool completion state change to broadcast to the frontend."""
    session_id: str
    tool_use_id: str
    result_count: int
    completed_at: datetime | None  # Timestamp of the latest tool_result
    extra: str | None = None  # Optional extra data (e.g. diff stats JSON for Edit tools)
    error: str | None = None  # Error message from tool_result (None = no error)
    tool_result_line_num: int | None = None  # Line number of the tool_result item


class AgentStoppedUpdate(NamedTuple):
    """Describes a subagent session whose process has naturally finished."""
    agent_session_id: str
    stopped_at: datetime


class ToolResultInfo(NamedTuple):
    """Provider-neutral output of ``extract_tool_result_info``.

    Each provider populates this from its own native tool-result block:
    Claude reads ``content[*].type == "tool_result"`` blocks; other
    providers parse their equivalents and fill the same shape.
    """
    tool_use_id: str | None
    is_error: bool
    error_text: str | None


class ItemGroupInfo(NamedTuple):
    """Group assignment for a single item, returned by :meth:`GroupState.process_item`."""
    group_head: int | None
    group_tail: int | None
    closed_items: list[Any] = []  # Items whose group was just closed


class ContentAnalysis(NamedTuple):
    """
    Single-pass extraction output used by the batch compute path.

    Replaces multiple individual content traversals with one structured
    payload. The shape is provider-neutral; each provider's
    :meth:`BaseSessionCompute.analyze_content` populates the fields from
    its own native content layout.
    """
    # Content visibility (any visible block: text, document, image, ...)
    has_visible_content: bool
    # First text block's text value, or None when missing
    text_content: str | None
    # Content is a string starting with a system XML prefix
    is_system_xml: bool
    # User message has a tool_result in content
    has_tool_result: bool
    # First tool_result's tool_use_id
    tool_result_id: str | None
    # Error from first tool_result (None when no error)
    tool_result_error: str | None
    # tool_use_id -> tool_name mapping
    tool_use_entries: dict[str, str]
    # [(tool_use_id, is_background)] for agent-spawning tool calls
    task_tool_uses: list[tuple[str, bool]]
    # Absolute file paths from tool_use inputs (for git resolution)
    file_paths: list[str]
    # Raw prefix/suffix detection (caller filters by kind)
    has_prefix: bool
    has_suffix: bool
    # (tool_use_id, agent_id) when the tool_result references a spawned agent
    tool_result_agent_info: tuple[str, str] | None


# =============================================================================
# Pure utilities — shared helpers that don't depend on provider parsing
# =============================================================================


_MARKDOWN_PATTERNS = [
    (re.compile(r'^#{1,6}\s+', re.MULTILINE), ''),  # Headers: # ## ### etc.
    (re.compile(r'\*\*(.+?)\*\*'), r'\1'),  # Bold: **text**
    (re.compile(r'__(.+?)__'), r'\1'),  # Bold: __text__
    (re.compile(r'\*(.+?)\*'), r'\1'),  # Italic: *text*
    (re.compile(r'_(.+?)_'), r'\1'),  # Italic: _text_
    (re.compile(r'~~(.+?)~~'), r'\1'),  # Strikethrough: ~~text~~
    (re.compile(r'`(.+?)`'), r'\1'),  # Inline code: `text`
    (re.compile(r'^\s*[-*+]\s+', re.MULTILINE), ''),  # Unordered list markers
    (re.compile(r'^\s*\d+\.\s+', re.MULTILINE), ''),  # Ordered list markers
    (re.compile(r'^\s*>\s*', re.MULTILINE), ''),  # Blockquotes
    (re.compile(r'\[([^\]]+)\]\([^)]+\)'), r'\1'),  # Links: [text](url) -> text
]


def strip_markdown(text: str) -> str:
    """Remove common markdown formatting from ``text``."""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def parse_timestamp_to_datetime(timestamp: str) -> datetime | None:
    """
    Parse an ISO timestamp string to a UTC-aware :class:`datetime`.

    Returns ``None`` for empty input or unparseable values. Handles the
    ``Z`` suffix by rewriting it as ``+00:00``.
    """
    if not timestamp:
        return None

    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None


# =============================================================================
# Agent link caches — cross-call memoization
# =============================================================================


# (session_id, agent_id) pairs whose AgentLink has already been created.
# Prevents redundant DB writes and short-circuits the matching loops.
AGENTS_LINKS_DONE_CACHE: set[tuple[str, str]] = set()

# Cached subagent prompts keyed by (parent_session_id, agent_id), used by
# the watcher to match a freshly-synced subagent against an existing Task
# tool_use in the parent session.
AGENTS_PROMPT_CACHE: dict[tuple[str, str], str] = {}


def mark_agent_link_done(session_id: str, agent_id: str) -> None:
    """Record that the AgentLink for this subagent has been created."""
    AGENTS_LINKS_DONE_CACHE.add((session_id, agent_id))
    uncache_agent_prompt(session_id, agent_id)


def is_agent_link_done(session_id: str, agent_id: str) -> bool:
    """Return ``True`` when the AgentLink for this subagent already exists."""
    return (session_id, agent_id) in AGENTS_LINKS_DONE_CACHE


def get_cached_agent_prompt(session_id: str, agent_id: str) -> str | None:
    """Read a cached subagent prompt, or ``None`` when missing."""
    return AGENTS_PROMPT_CACHE.get((session_id, agent_id))


def cache_agent_prompt(session_id: str, agent_id: str, prompt: str) -> None:
    """Store a subagent prompt for later matching against parent tool_uses."""
    AGENTS_PROMPT_CACHE[(session_id, agent_id)] = prompt


def uncache_agent_prompt(session_id: str, agent_id: str) -> None:
    """Drop a cached subagent prompt (e.g. after a successful link)."""
    AGENTS_PROMPT_CACHE.pop((session_id, agent_id), None)


# =============================================================================
# GroupState — provider-agnostic state machine for collapsible groups
# =============================================================================


class GroupState:
    """
    Tracks group state during sequential item processing.

    A group is "open" when:

    - the previous item was COLLAPSIBLE, or
    - the previous ALWAYS item had a collapsible suffix (potential group start).

    The state machine operates purely on already-extracted metadata
    (``display_level``, ``has_prefix``, ``has_suffix``) so it is
    provider-agnostic — each provider populates those flags from its
    own native content layout.

    Usage::

        state = GroupState()
        for item in items:
            info = state.process_item(item.line_num, display_level, has_prefix, has_suffix, item)
            item.group_head = info.group_head
            item.group_tail = info.group_tail
        state.finalize()  # Close any pending group
    """

    def __init__(self) -> None:
        # Current open group (COLLAPSIBLE items accumulating)
        self._group_head: int | None = None
        self._group_items: list[tuple[int, Any]] = []  # (line_num, item_ref)

        # Pending ALWAYS with suffix (might start a group)
        self._pending_suffix: tuple[int, Any] | None = None  # (line_num, item_ref)

    def has_open_group(self) -> bool:
        """Check if there's an open group that the next item could join."""
        return self._group_head is not None or self._pending_suffix is not None

    def get_current_head(self) -> int | None:
        """Get the head of the current open group."""
        if self._group_head is not None:
            return self._group_head
        if self._pending_suffix is not None:
            return self._pending_suffix[0]
        return None

    def process_item(
        self,
        line_num: int,
        display_level: ItemDisplayLevel,
        has_prefix: bool,
        has_suffix: bool,
        item_ref: Any = None,
    ) -> ItemGroupInfo:
        """
        Process a single item and return its group assignment.

        Args:
            line_num: The item's line number
            display_level: ALWAYS, COLLAPSIBLE, or DEBUG_ONLY
            has_prefix: True if ALWAYS item has collapsible prefix
            has_suffix: True if ALWAYS item has collapsible suffix
            item_ref: Reference to item object (for batch updates)

        Returns:
            ItemGroupInfo with group_head and group_tail assignments
        """
        if display_level == ItemDisplayLevel.DEBUG_ONLY:
            # DEBUG_ONLY: transparent to groups, no participation
            return ItemGroupInfo(group_head=None, group_tail=None)

        if display_level == ItemDisplayLevel.COLLAPSIBLE:
            return self._process_collapsible(line_num, item_ref)

        # ALWAYS
        return self._process_always(line_num, has_prefix, has_suffix, item_ref)

    def _process_collapsible(self, line_num: int, item_ref: Any) -> ItemGroupInfo:
        """Process a COLLAPSIBLE item."""
        # Check if we're connecting to a pending ALWAYS suffix
        if self._pending_suffix is not None:
            suffix_line, suffix_ref = self._pending_suffix
            self._pending_suffix = None

            # The ALWAYS suffix starts this group
            self._group_head = suffix_line
            self._group_items = [(suffix_line, suffix_ref), (line_num, item_ref)]
            return ItemGroupInfo(group_head=suffix_line, group_tail=None)

        # Join existing group or start new one
        if self._group_head is not None:
            # Continue existing group
            self._group_items.append((line_num, item_ref))
            return ItemGroupInfo(group_head=self._group_head, group_tail=None)
        else:
            # Start new group
            self._group_head = line_num
            self._group_items = [(line_num, item_ref)]
            return ItemGroupInfo(group_head=line_num, group_tail=None)

    def _process_always(
        self, line_num: int, has_prefix: bool, has_suffix: bool, item_ref: Any
    ) -> ItemGroupInfo:
        """Process an ALWAYS item."""
        result_head: int | None = None
        closed_items: list[Any] = []
        joined_via_prefix = False

        # Handle prefix: can join an open group
        if has_prefix and self.has_open_group():
            result_head = self.get_current_head()
            joined_via_prefix = True

            # Add to group items for tail update (but track that this is the joining ALWAYS)
            if self._pending_suffix is not None:
                # Connect pending suffix to this prefix
                suffix_line, suffix_ref = self._pending_suffix
                self._group_items = [(suffix_line, suffix_ref)]
                self._group_head = suffix_line
                self._pending_suffix = None
            # Don't add the current ALWAYS to _group_items - it joins but doesn't get group_tail

        # ALWAYS always terminates any group before it
        if self._group_items:
            # Determine tail: this item if it joined via prefix, else last item in group
            if joined_via_prefix:
                tail = line_num
            else:
                tail = self._group_items[-1][0]

            # Update all items in the group (not including current ALWAYS)
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    closed_items.append(ref)

            # Reset group state
            self._group_items = []
            self._group_head = None

        # Also close pending suffix if not joined by this item's prefix
        if self._pending_suffix is not None and not joined_via_prefix:
            # Pending suffix was not connected, close it as orphan
            suffix_line, suffix_ref = self._pending_suffix
            if suffix_ref is not None:
                # Suffix stays orphan (group_tail already None)
                closed_items.append(suffix_ref)
            self._pending_suffix = None

        # Handle suffix: might start a new group
        if has_suffix:
            self._pending_suffix = (line_num, item_ref)

        # ALWAYS item itself doesn't get group_tail from this operation
        # group_tail for ALWAYS is only set when its suffix connects to something later
        return ItemGroupInfo(group_head=result_head, group_tail=None, closed_items=closed_items)

    def finalize(self) -> list[Any]:
        """
        Finalize any open groups at end of processing.

        Returns:
            List of item references that were updated (for batch save)
        """
        updated = []

        # Close any open COLLAPSIBLE group
        if self._group_items:
            tail = self._group_items[-1][0]
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    updated.append(ref)
            self._group_items = []
            self._group_head = None

        # Pending ALWAYS suffix stays orphan (group_tail = None)
        if self._pending_suffix is not None:
            _, ref = self._pending_suffix
            if ref is not None:
                updated.append(ref)
            self._pending_suffix = None

        return updated


# =============================================================================
# BaseSessionCompute — provider-agnostic compute surface
# =============================================================================


class BaseSessionCompute:
    """
    Abstract per-provider session compute.

    Each provider subclasses this and overrides the extraction methods
    (the ones that parse a native JSONL line into TwiCC's neutral
    structures). The orchestration methods (group state machine, agent /
    tool-result link creation, batch compute, watcher live sync) will be
    implemented concretely in this base class in later steps so every
    provider inherits them for free.

    Step 1 (this commit) only declares the surface — every method raises
    :class:`NotImplementedError`. Steps 2-4 incrementally fill it in:

    - Step 2 wires the Claude Code subclass to the live extraction +
      :meth:`compute_item_metadata_live` and link methods.
    - Step 3 migrates the batch path (:meth:`compute_session_metadata`,
      :meth:`apply_session_complete`).
    - Step 4 migrates the watcher's :meth:`sync_session_items_from_file`.
    """

    provider: ClassVar[Provider]

    # ------------------------------------------------------------------
    # Extraction surface — overridden by each provider
    # ------------------------------------------------------------------

    def transform_inline(self, parsed_json: dict) -> str | None:
        """
        Optionally rewrite a parsed item in place before metadata computation.

        Used by Claude Code to normalise legacy or non-standard formats
        (``<task-notification>``, ``<local-command-stdout>``) into the
        standard tool_result / assistant_message shape that the rest of
        the compute pipeline expects.

        Returns the new serialised JSON string when a transformation was
        applied (caller updates ``SessionItem.content``), or ``None`` when
        the item was left untouched.
        """
        raise NotImplementedError

    def analyze_content(self, parsed_json: dict) -> ContentAnalysis:
        """
        Single-pass content extraction used by the batch compute path.

        Returns a :class:`ContentAnalysis` populated from the provider's
        native content layout. Each provider produces the same neutral
        shape so the batch orchestration stays format-agnostic.
        """
        raise NotImplementedError

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        """Determine the :class:`ItemKind` for a parsed JSONL line, or ``None``."""
        raise NotImplementedError

    def compute_item_display_level(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> int:
        """Determine the :class:`ItemDisplayLevel` for a parsed JSONL line."""
        raise NotImplementedError

    def compute_item_metadata(self, parsed_json: dict) -> dict:
        """
        Compute ``{display_level, kind}`` for one item.

        Convenience wrapper that calls :meth:`compute_item_kind` and then
        :meth:`compute_item_display_level`. Providers usually inherit this
        as-is unless they need extra fields in the metadata dict.
        """
        raise NotImplementedError

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        """Return the item's timestamp as a UTC-aware ``datetime``, or ``None``."""
        raise NotImplementedError

    def extract_title_from_user_message(self, parsed_json: dict) -> str | None:
        """Extract a session title candidate from a user message, or ``None``."""
        raise NotImplementedError

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        """
        Return a dict with the runtime environment fields carried by ``parsed_json``.

        Keys (each optional, missing keys are equivalent to ``None``):

        - ``cwd``: working directory recorded for the line
        - ``cwd_git_branch``: native git branch reported alongside ``cwd``
        - ``model``: model identifier last seen
        - ``slug``: session slug last seen
        """
        raise NotImplementedError

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],
    ) -> None:
        """
        Compute cost / context usage and assign them on ``item`` in place.

        Handles deduplication via ``seen_message_ids``: cost is only
        assigned the first time a given ``message_id`` is encountered
        (providers that stream multiple lines per API call share the
        same ``message_id``).
        """
        raise NotImplementedError

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        """Return ``True`` when the line carries a tool_result block."""
        raise NotImplementedError

    def extract_tool_use_entries(self, parsed_json: dict) -> dict[str, str]:
        """Return a ``{tool_use_id: tool_name}`` mapping for the line, possibly empty."""
        raise NotImplementedError

    def extract_tool_result_info(self, parsed_json: dict) -> ToolResultInfo | None:
        """Return :class:`ToolResultInfo` for the first tool_result, or ``None``."""
        raise NotImplementedError

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        """Return ``(tool_use_id, agent_id)`` when the tool_result links to a subagent."""
        raise NotImplementedError

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        """Return ``[(tool_use_id, is_background)]`` for agent-spawning tool_uses."""
        raise NotImplementedError

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        """Return ``[(tool_use_id, prompt, is_background)]`` for agent-spawning tool_uses."""
        raise NotImplementedError

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        """
        Return absolute file/directory paths referenced by tool_use blocks.

        Used by :meth:`resolve_git_for_item` to locate the git root
        relevant to the item.
        """
        raise NotImplementedError

    def compute_file_change_stats(self, parsed_json: dict) -> str | None:
        """
        Compute diff stats (added / removed lines) for an Edit/Write tool_result.

        Returns a JSON string ready to store in ``ToolResultLink.extra``,
        or ``None`` when the data is unavailable.
        """
        raise NotImplementedError

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        """Return ``(has_collapsible_prefix, has_collapsible_suffix)`` for an ALWAYS item."""
        raise NotImplementedError

    def resolve_git_for_item(
        self, parsed_json: dict, *, use_cache: bool = True
    ) -> tuple[str, str] | None:
        """
        Resolve ``(git_directory, git_branch)`` for the item, or ``None``.

        Default implementation will live in this base class once step 2
        lands — it walks the paths returned by
        :meth:`extract_paths_from_tool_uses` through
        :func:`twicc.git.resolve_git_from_path` and picks the most common
        resolution. Providers rarely need to override.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Live (watcher) orchestration — concrete in later steps
    # ------------------------------------------------------------------

    def find_open_group_head(
        self, session_id: str, before_line_num: int
    ) -> int | None:
        """Find the head of any open group before ``before_line_num``, or ``None``."""
        raise NotImplementedError

    def compute_item_metadata_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> set[int]:
        """
        Compute group membership for ``item`` during live watcher sync.

        Updates ``item.group_head`` / ``item.group_tail`` (and possibly
        ``git_directory`` / ``git_branch``) in place, and returns the
        set of pre-existing item line numbers whose ``group_tail`` was
        updated as a side effect.
        """
        raise NotImplementedError

    def create_tool_result_link_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> ToolResultUpdate | None:
        """Create a :class:`~twicc.core.models.ToolResultLink` during live sync."""
        raise NotImplementedError

    def check_agent_naturally_stopped(
        self, session_id: str, tool_result_update: ToolResultUpdate
    ) -> AgentStoppedUpdate | None:
        """Detect when a subagent has finished after the latest tool_result arrived."""
        raise NotImplementedError

    def create_agent_link_from_tool_result(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> AgentLinkUpdate | None:
        """Create an :class:`~twicc.core.models.AgentLink` from a tool_result with agentId."""
        raise NotImplementedError

    def create_agent_link_from_subagent(
        self,
        parent_session_id: str,
        agent_id: str,
        agent_prompt: str,
    ) -> AgentLinkUpdate | None:
        """
        Create an :class:`~twicc.core.models.AgentLink` by matching a subagent prompt.

        Used by the subagent watcher path: when a fresh subagent file is
        synced, find the parent's matching tool_use and link them.
        """
        raise NotImplementedError

    def create_agent_link_from_tool_use(
        self,
        session_id: str,
        item: SessionItem,
        parsed_json: dict,
    ) -> list[AgentLinkUpdate]:
        """
        Create AgentLinks for newly-synced tool_uses against existing subagents.

        Handles the race condition where the subagent file landed before
        the parent session's Task tool_use line was synced.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Batch orchestration — concrete in later steps
    # ------------------------------------------------------------------

    def compute_session_metadata(self, session_id: str, result_queue) -> None:
        """
        Compute metadata for every item in a session and push the result on ``result_queue``.

        Runs in the multiprocessing worker. Does not touch the DB
        directly; the caller (``apply_session_complete``) consumes the
        queue and applies the changes.
        """
        raise NotImplementedError

    def apply_session_complete(self, msg: dict) -> None:
        """
        Apply a ``session_complete`` payload produced by :meth:`compute_session_metadata`.

        Performs all DB writes (item updates, link diffs, session field
        updates, project metadata) in the main process.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Watcher orchestration — concrete in later steps
    # ------------------------------------------------------------------

    def sync_session_items_from_file(
        self,
        session: Session,
        file_path: Path,
    ) -> tuple[
        list[int],
        list[int],
        list[AgentLinkUpdate],
        list[ToolResultUpdate],
        list[AgentStoppedUpdate],
    ]:
        """
        Synchronise new lines from ``file_path`` into ``session``.

        Reads from ``session.last_offset``, transforms / parses every new
        line, computes metadata, persists items, links, lifecycle
        timestamps, costs, and returns the broadcast payload tuple
        ``(new_line_nums, modified_line_nums, agent_link_updates,
        tool_result_updates, agent_stopped_updates)``.
        """
        raise NotImplementedError
