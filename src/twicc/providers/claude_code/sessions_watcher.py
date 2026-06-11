"""
Claude Code file watcher.

Thin :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` subclass
that plugs in Claude Code's directory layout, compute object, agent manager
hook, and project-directory tracking. Everything else (watchfiles loop, ORM
updates, broadcasts, search indexing, polling) lives in the base.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import ClassVar

import orjson
from asgiref.sync import sync_to_async
from watchfiles import Change

from twicc.core.models import SessionType
from twicc.core.serializers import serialize_project

from .compute import get_compute as _get_compute
from .helpers import ClaudeCodeHelpers
from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    ParsedSessionFile,
    broadcast_message,
    get_project_by_id,
)

logger = logging.getLogger(__name__)


# Real subagent files are named agent-a<hex>.jsonl (e.g. agent-a6c7d21.jsonl).
# Sidechain files like agent-acompact-<hex>.jsonl or agent-aprompt_suggestion-<hex>.jsonl are excluded.
_REAL_SUBAGENT_RE = re.compile(r"^agent-a[0-9a-f]+\.jsonl$")


class ClaudeCodeSessionsWatcher(BaseSessionsWatcher):
    """File watcher for Claude Code's ``~/.claude/projects/`` layout.

    Layouts handled:

    - ``project_id/session_id.jsonl`` → :class:`SessionType.SESSION`
    - ``project_id/session_id/subagents/agent-a<hex>.jsonl`` →
      :class:`SessionType.SUBAGENT` (sidechain files like
      ``agent-acompact-*`` or ``agent-aprompt_suggestion-*`` are skipped).

    Direct children of :attr:`projects_dir` are project directories: their
    creation/deletion is handled by :meth:`maybe_handle_special_change`,
    which broadcasts a ``project_updated`` event when the working
    directory referenced by the project disappears.
    """

    projects_dir: ClassVar[Path] = ClaudeCodeHelpers.PROJECTS_DIR

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        try:
            relative = path.relative_to(self.projects_dir)
        except ValueError:
            return None

        parts = relative.parts

        if len(parts) == 2:
            # Format: project_id/xxx.jsonl
            project_id, filename = parts
            if filename.startswith("agent-"):
                # Old format agents at project level - ignore
                return None
            if filename.endswith(".jsonl"):
                session_id = filename.removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    session_id,
                    SessionType.SESSION,
                    file_path=str(relative),
                )

        elif len(parts) == 4:
            # Format: project_id/session_id/subagents/agent-a<hex>.jsonl
            # Sidechain files (acompact-*, aprompt_suggestion-*) are excluded.
            project_id, parent_session_id, subdir, filename = parts
            if (
                subdir == "subagents"
                and _REAL_SUBAGENT_RE.match(filename) is not None
            ):
                agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    agent_id,
                    SessionType.SUBAGENT,
                    file_path=str(relative),
                    parent_session_id=parent_session_id,
                )

        return None

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()

    async def maybe_handle_special_change(
        self,
        path: Path,
        change_type: Change,
        channel_layer,
    ) -> bool:
        """Handle direct children of :attr:`projects_dir` as project dirs."""
        if path.parent != self.projects_dir:
            return False
        if not (path.is_dir() or change_type == Change.deleted):
            return False
        await self._sync_project_and_broadcast(path, channel_layer)
        return True

    async def _sync_project_and_broadcast(
        self,
        path: Path,
        channel_layer,
    ) -> None:
        """
        React to a project directory being created or deleted.

        Projects are NOT created eagerly here. They are created lazily
        when the first session with content appears (in
        ``sync_and_broadcast``). This avoids polluting the project list
        with empty folders (e.g. folders left behind after Claude
        sublimates old sessions).

        This handler only updates the stale flag on existing projects.
        Stale is based on working directory existence, not the provider folder.
        """
        project = await get_project_by_id(path.name)
        if project is None:
            return

        should_be_stale = (
            project.directory is not None and not os.path.isdir(project.directory)
        )
        if project.stale != should_be_stale:
            project.stale = should_be_stale
            await sync_to_async(project.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })

    async def _after_tool_result_broadcast(self, update: ToolResultUpdate) -> None:
        """Drop the active-tool entry left over by hooks that never fired.

        PostToolUse / PostToolUseFailure hooks may not run for tools that
        the CLI rejects via its own validation (e.g. permission denials
        that synthesise an ``is_error`` tool_result without ever invoking
        the tool). When the resulting tool_result lands in the JSONL,
        we explicitly evict the entry from the agent's ``_active_tools``
        registry so the UI doesn't keep showing a phantom in-flight tool.
        Hooks fire faster when they do fire; this catches the gaps.
        """
        from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

        manager = get_claude_code_agent_manager()
        await manager.discard_active_tool(update.session_id, update.tool_use_id)

    async def _after_compaction_synced(self, session_id: str) -> None:
        """End a hybrid agent's turn when a compaction lands.

        A manually-triggered ``/compact`` writes no ``turn_duration`` line, so
        without this the JSONL bridge would leave the agent in ASSISTANT_TURN
        until the next real turn (same issue Codex solves with this hook).
        ``handle_hybrid_jsonl_signals`` ignores non-hybrid sessions, so no
        hybrid check is needed here.
        """
        from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
        from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

        asyncio.create_task(
            get_claude_code_agent_manager().handle_hybrid_jsonl_signals(
                session_id,
                HybridJsonlSignals(user_message=False, turn_end=True, tool_results=False),
            ),
            name=f"hybrid-compact-turn-end-{session_id}",
        )

    async def _after_new_lines_synced(
        self,
        session,
        new_line_nums: list[int],
        tool_result_updates: list[ToolResultUpdate],
    ) -> None:
        """JSONL → state bridge for hybrid sessions.

        Derives the batch's :class:`HybridJsonlSignals` from the freshly
        computed items and hands them to the agent manager. Fire-and-forget:
        the derivation (a DB read) and the agent transitions run in a task
        so the ingest path never blocks on agent locks. Latency stays at
        inotify level (ms), same as the live UI updates.
        """
        if not session.hybrid:
            return
        asyncio.create_task(
            self._bridge_hybrid_signals(
                session.id, new_line_nums, bool(tool_result_updates),
            ),
            name=f"hybrid-jsonl-bridge-{session.id}",
        )

    async def _bridge_hybrid_signals(
        self,
        session_id: str,
        new_line_nums: list[int],
        has_tool_results: bool,
    ) -> None:
        try:
            from twicc.core.enums import ItemKind
            from twicc.core.models import SessionItem

            def _derive() -> tuple[bool, bool]:
                user_message = False
                turn_end = False
                rows = (
                    SessionItem.objects
                    .filter(session_id=session_id, line_num__in=new_line_nums)
                    .values_list("kind", "content")
                )
                for kind, content in rows:
                    if kind == ItemKind.USER_MESSAGE:
                        user_message = True
                    elif kind == ItemKind.SYSTEM and content:
                        if '"turn_duration"' in content:
                            try:
                                parsed = orjson.loads(content)
                            except Exception:
                                continue
                            if (
                                parsed.get("type") == "system"
                                and parsed.get("subtype") == "turn_duration"
                            ):
                                turn_end = True
                        elif "<local-command-stdout>" in content:
                            # A local slash command's stdout marks its end.
                            # Slash-command lines classify as USER_MESSAGE
                            # (TwiCC shows them in the conversation), which
                            # flips the agent to ASSISTANT_TURN above — but
                            # local commands (/model, /rename, …) never write
                            # a turn_duration, so without this the agent
                            # would look busy forever after a pasted command.
                            turn_end = True
                return user_message, turn_end

            user_message, turn_end = await asyncio.to_thread(_derive)
            if not (user_message or turn_end or has_tool_results):
                return

            from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
            from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

            await get_claude_code_agent_manager().handle_hybrid_jsonl_signals(
                session_id,
                HybridJsonlSignals(
                    user_message=user_message,
                    turn_end=turn_end,
                    tool_results=has_tool_results,
                ),
            )
        except Exception:
            logger.exception(
                "Hybrid JSONL bridge failed for session %s", session_id,
            )


# ---- Singleton accessor ----

_watcher: ClaudeCodeSessionsWatcher | None = None


def get_watcher() -> ClaudeCodeSessionsWatcher:
    """Return the process-local Claude Code watcher singleton.

    Callers (orchestrator, SDK agent for fast-poll requests) should go
    through this rather than instantiating their own watcher, so the
    stop/boost events and fast-poll deadline are shared.
    """
    global _watcher
    if _watcher is None:
        _watcher = ClaudeCodeSessionsWatcher()
    return _watcher
