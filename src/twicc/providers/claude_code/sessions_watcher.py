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

from twicc.core.models import SessionType, Workflow
from twicc.core.serializers import serialize_project, serialize_session

from .compute import (
    WORKFLOW_FILE_PREFIX,
    WORKFLOW_FILE_SUFFIX,
    get_compute as _get_compute,
)
from .helpers import ClaudeCodeHelpers
from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    ParsedSessionFile,
    broadcast_message,
    get_project_by_id,
    get_session_by_id,
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
    - ``project_id/session_id/subagents/workflows/<run_id>/agent-a<hex>.jsonl``
      → :class:`SessionType.SUBAGENT` with the composite id
      ``<run_id>:<agent_id>`` (a workflow subagent).

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

        elif len(parts) == 6:
            # Format: project_id/session_id/subagents/workflows/<run_id>/agent-a<hex>.jsonl
            # A workflow subagent (spawned by the workflow engine, not by a Task
            # tool, so it has no AgentLink and never appears in the parent's
            # conversation flow). Its Session.id is the composite
            # ``<run_id>:<agent_id>`` — unique across runs and distinguishable
            # from a normal subagent (whose id is the bare agent hex). Sidechain
            # files are excluded by the same name regex.
            project_id, parent_session_id, subdir, wf_subdir, run_id, filename = parts
            if (
                subdir == "subagents"
                and wf_subdir == "workflows"
                and _REAL_SUBAGENT_RE.match(filename) is not None
            ):
                agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
                return ParsedSessionFile(
                    project_id,
                    f"{run_id}:{agent_id}",
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
        """Handle workflow run files and project directories.

        Two non-``.jsonl`` patterns the base loop would otherwise skip:

        - ``<project>/<session>/workflows/wf_*.json`` — a workflow run
          artifact. Its appearance latches the owning session's
          ``has_workflows`` flag (one-way; deletions are ignored) and upserts
          the :class:`Workflow` row mirroring the file (the runtime rewrites it
          on every progress tick, so writes flow through here live).
        - direct children of :attr:`projects_dir` — project directories,
          whose creation/deletion refreshes the project ``stale`` flag.
        """
        # Workflow run file: a cheap path-shape check (no I/O) runs on every
        # event, so keep it first and bail fast for the overwhelmingly common
        # ``.jsonl`` append. Deletions never reset the flag.
        if change_type != Change.deleted:
            workflow_session_id = self._workflow_json_session_id(path)
            if workflow_session_id is not None:
                await self._handle_workflow_run(workflow_session_id, path, change_type, channel_layer)
                return True

            # TEMP probe — observe whether the run's journal.jsonl grows live
            # during the run or lands all at once at the end. Remove once settled.
            journal_run_id = self._workflow_journal_run_id(path)
            if journal_run_id is not None:
                await self._journal_probe(journal_run_id, path, change_type)
                return True

        # Direct children of projects_dir are project dirs.
        if path.parent != self.projects_dir:
            return False
        if not (path.is_dir() or change_type == Change.deleted):
            return False
        await self._sync_project_and_broadcast(path, channel_layer)
        return True

    def _workflow_json_session_id(self, path: Path) -> str | None:
        """Owning session id if ``path`` is a workflow run file.

        Matches ``<project_id>/<session_id>/workflows/wf_*.json`` relative to
        :attr:`projects_dir` — a ``wf_*.json`` directly at the root of a
        top-level session's ``workflows/`` folder. Returns ``None`` for
        anything else (including nested paths like ``workflows/scripts/*`` or
        ``subagents/workflows/*``, which have a different depth). Pure path
        inspection, no filesystem access.
        """
        try:
            parts = path.relative_to(self.projects_dir).parts
        except ValueError:
            return None
        if (
            len(parts) == 4
            and parts[2] == "workflows"
            and parts[3].startswith(WORKFLOW_FILE_PREFIX)
            and parts[3].endswith(WORKFLOW_FILE_SUFFIX)
        ):
            return parts[1]
        return None

    def _workflow_journal_run_id(self, path: Path) -> str | None:
        """Run id if ``path`` is a run's ``journal.jsonl``, else ``None``.

        Matches ``<project>/<session>/subagents/workflows/<run_id>/journal.jsonl``
        relative to :attr:`projects_dir`. Pure path inspection. (TEMP — supports
        the journal-write probe.)
        """
        try:
            parts = path.relative_to(self.projects_dir).parts
        except ValueError:
            return None
        if (
            len(parts) == 6
            and parts[2] == "subagents"
            and parts[3] == "workflows"
            and parts[5] == "journal.jsonl"
        ):
            return parts[4]
        return None

    async def _journal_probe(self, run_id: str, path: Path, change_type) -> None:
        """Log the run journal's size/line counts on each write (TEMP probe)."""
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError:
            return
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        started = sum(1 for ln in lines if '"type":"started"' in ln)
        result = sum(1 for ln in lines if '"type":"result"' in ln)
        logger.info(
            "[journal-probe] run=%s change=%s bytes=%d lines=%d started=%d result=%d",
            run_id, getattr(change_type, "name", change_type), len(raw),
            len(lines), started, result,
        )

    async def _handle_workflow_run(self, session_id: str, path: Path, change_type, channel_layer) -> None:
        """React to a ``wf_*.json`` write: latch ``has_workflows`` + persist the run.

        No-op when the owning session row isn't synced yet (the boot backfill in
        ``initial_sync`` picks it up). One session fetch shared by both steps.
        """
        session = await get_session_by_id(session_id)
        if session is None:
            return
        await self._latch_session_workflows(session, channel_layer)
        saved = await self._upsert_workflow_run(session.id, path, change_type)
        # Tell open Workflows tabs to refetch — the run was created or its
        # envelope changed (the engine rewrites the file on each progress tick).
        if saved and not session.hidden:
            await broadcast_message(channel_layer, {
                "type": "workflow_changed",
                "session_id": session.id,
                "project_id": session.project_id,
                "run_id": path.stem,
            })

    async def _latch_session_workflows(self, session, channel_layer) -> None:
        """Set ``has_workflows=True`` on a session and broadcast the change.

        No-op when already latched. One-way: never resets the flag.
        """
        if session.has_workflows:
            return
        session.has_workflows = True
        await sync_to_async(session.save)(update_fields=["has_workflows"])
        if not session.hidden:
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

    async def _upsert_workflow_run(self, session_id: str, path: Path, change_type) -> bool:
        """Mirror a ``wf_*.json`` file into its :class:`Workflow` row (upsert by run_id).

        ``run_id`` is the filename stem (``wf_<hex>``). The file is read off the
        event loop and validated with ``orjson`` before storing — a partial
        write caught mid-flush fails to parse and is skipped (the next tick
        rewrites it and re-triggers this path). Returns ``True`` when the row was
        written, so the caller knows to broadcast.
        """
        run_id = path.stem
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError:
            return False
        try:
            parsed = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return False

        # TEMP probe — confirm empirically whether the wf_*.json is written once
        # (at completion) or refreshed during the run. Every detected write logs
        # its change type + the state carried by the content. Remove once settled.
        agents = [
            w for w in (parsed.get("workflowProgress") or [])
            if isinstance(w, dict) and w.get("type") == "workflow_agent"
        ]
        done = sum(1 for w in agents if w.get("state") == "done")
        logger.info(
            "[wf-probe] run=%s change=%s bytes=%d status=%s agentCount=%s progress=%d/%d durationMs=%s",
            run_id, getattr(change_type, "name", change_type), len(raw),
            parsed.get("status"), parsed.get("agentCount"), done, len(agents), parsed.get("durationMs"),
        )

        await sync_to_async(self._save_workflow_run)(session_id, run_id, raw)
        return True

    @staticmethod
    def _save_workflow_run(session_id: str, run_id: str, raw: str) -> None:
        Workflow.objects.update_or_create(
            run_id=run_id,
            defaults={"session_id": session_id, "raw_json": raw},
        )

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
                HybridJsonlSignals(turn_end=True),
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

            from twicc.providers.claude_code.compute import (
                INTERRUPTION_MARKER_PREFIX,
                extract_command,
                extract_text_from_content,
                get_message_content,
                is_interruption_marker,
            )

            def _derive() -> tuple[bool, bool, bool, bool]:
                user_message = False
                turn_end = False
                command_message = False
                local_command_ack = False
                rows = (
                    SessionItem.objects
                    .filter(session_id=session_id, line_num__in=new_line_nums)
                    .values_list("kind", "content")
                )
                for kind, content in rows:
                    if kind == ItemKind.USER_MESSAGE:
                        # Slash-command echoes (<command-name> lines) start a
                        # local command, not an assistant turn — keep them
                        # apart so their ack can close exactly what they
                        # opened. Sniff then parse: a real prompt could quote
                        # the tag.
                        if "<command-name>" in content:
                            try:
                                parsed = orjson.loads(content)
                                text = extract_text_from_content(
                                    get_message_content(parsed),
                                )
                            except Exception:
                                text = None
                            if text and extract_command(text):
                                command_message = True
                                continue
                        user_message = True
                    elif kind == ItemKind.API_ERROR and content:
                        # Two shapes both classify as API_ERROR: intermediate
                        # retry progress (type=system, subtype=api_error,
                        # retryAttempt=N/M) which must NOT end the turn — the CLI
                        # is still retrying — and the terminal surfaced error
                        # (isApiErrorMessage=true) shown once retries are
                        # exhausted or the error is non-retryable. Only the
                        # terminal one ends the turn: the interactive CLI returns
                        # to an empty composer but never writes a turn_duration,
                        # so without this a hybrid agent would stay stuck in
                        # ASSISTANT_TURN. Sniff then parse.
                        if '"isApiErrorMessage"' in content:
                            try:
                                parsed = orjson.loads(content)
                            except Exception:
                                continue
                            if parsed.get("isApiErrorMessage"):
                                turn_end = True
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
                            # NOT folded into turn_end: mid-turn it must not
                            # end anything (the agent handler decides).
                            local_command_ack = True
                        elif INTERRUPTION_MARKER_PREFIX in content:
                            # Interruption breadcrumb (classified SYSTEM by
                            # compute): the turn was aborted — it will never
                            # write a turn_duration. Sniff then parse: the
                            # prefix could appear inside arbitrary content.
                            try:
                                parsed = orjson.loads(content)
                                text = extract_text_from_content(
                                    get_message_content(parsed),
                                )
                            except Exception:
                                continue
                            if (
                                parsed.get("type") == "user"
                                and text
                                and is_interruption_marker(text)
                            ):
                                turn_end = True
                return user_message, turn_end, command_message, local_command_ack

            user_message, turn_end, command_message, local_command_ack = (
                await asyncio.to_thread(_derive)
            )
            if not (
                user_message or turn_end or command_message
                or local_command_ack or has_tool_results
            ):
                return

            from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
            from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

            await get_claude_code_agent_manager().handle_hybrid_jsonl_signals(
                session_id,
                HybridJsonlSignals(
                    user_message=user_message,
                    turn_end=turn_end,
                    tool_results=has_tool_results,
                    command_message=command_message,
                    local_command_ack=local_command_ack,
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
