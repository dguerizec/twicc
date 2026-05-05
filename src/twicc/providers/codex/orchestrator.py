"""
Codex provider orchestrator.

Owns the Codex CLI auth check task. There is no JSONL watcher, initial
sync, agent runtime, or pricing yet — those will land when the agent
runtime is wired. This orchestrator exists today only so the auth task
shares the same lifecycle plumbing as Claude Code's.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.core.enums import Provider
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.codex.auth_task import start_auth_task, stop_auth_task

logger = logging.getLogger(__name__)


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


class CodexOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Codex provider tasks (auth check only, for now)."""

    provider = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        self._auth_check_task: asyncio.Task | None = None

    async def start(self, shutdown_event: asyncio.Event) -> None:
        """Launch the Codex auth check task."""
        self._auth_check_task = asyncio.create_task(start_auth_task())

    async def shutdown(self) -> None:
        """Stop the Codex auth check task."""
        if self._auth_check_task is not None:
            logger.info("Stopping Codex auth check task...")
            stop_auth_task()
            await _cancel_task(self._auth_check_task, "Codex auth check task")
