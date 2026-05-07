"""
Claude Code file watcher.

Thin :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` subclass
that plugs in Claude Code's directory layout, compute object, and agent
manager hook. Everything else (watchfiles loop, ORM updates, broadcasts,
search indexing, polling) lives in the base.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import ClassVar

from twicc.core.models import SessionType

from .compute import get_compute as _get_compute
from .helpers import ClaudeCodeHelpers
from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    ParsedPath,
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
    """

    projects_dir: ClassVar[Path] = ClaudeCodeHelpers.PROJECTS_DIR

    def parse_jsonl_path(self, path: Path) -> ParsedPath | None:
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
                return ParsedPath(
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
                return ParsedPath(
                    project_id,
                    agent_id,
                    SessionType.SUBAGENT,
                    file_path=str(relative),
                    parent_session_id=parent_session_id,
                )

        return None

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()

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
