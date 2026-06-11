"""Hybrid Claude Code agent: drives the interactive CLI (TUI) in tmux.

Implements the :class:`BaseAgent` contract without an SDK message loop.
State transitions are pushed from the outside:

- the sessions watcher's JSONL bridge calls :meth:`on_jsonl_user_message` /
  :meth:`on_jsonl_progress` / :meth:`on_jsonl_turn_end` as lines land;
- the hybrid hooks watcher delivers the single injected ``PermissionRequest``
  hook via :meth:`on_permission_request`;
- a light liveness monitor polls the tmux pane and transitions to DEAD when
  claude exits.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from asgiref.sync import sync_to_async

from twicc.agent.base_agent import BaseAgent, StateChangeCallback
from twicc.agent.states import AgentState, PendingRequest
from twicc.core.enums import Provider
from twicc.paths import get_session_hybrid_dir

from . import tmux as hybrid_tmux
from .launch import build_argv, write_addendum_file

logger = logging.getLogger(__name__)

# The trust dialog swallows pasted text entirely (verified empirically:
# pasting while it is up leaves the composer empty after the dialog is
# answered). The first paste therefore waits until no trust dialog is on
# screen. The pattern matches both the question and the confirm option of
# the current dialog wording, with older variants covered by "do you trust".
_TRUST_DIALOG_RE = re.compile(r"trust this folder|do you trust", re.IGNORECASE)


class HybridClaudeAgent(BaseAgent):
    """Claude Code session driven by the interactive CLI in tmux."""

    provider = Provider.CLAUDE_CODE
    # Discriminator used by the manager / WS layer to branch hybrid-specific
    # behavior without isinstance checks across modules.
    is_hybrid = True

    # Seconds before the first paste: the TUI must be fully drawn or the
    # paste is lost. Verified: the welcome screen (or the trust dialog) is
    # rendered well within 8s on this machine; the trust-dialog wait below
    # then covers the only known blocking dialog.
    FIRST_PASTE_DELAY = 8.0
    # Trust-dialog polling: the user answers it inside the embedded
    # terminal, which can take a while. Poll the pane until the dialog is
    # gone, then settle before pasting.
    TRUST_DIALOG_POLL = 2.0
    TRUST_DIALOG_TIMEOUT = 600.0
    TRUST_DIALOG_SETTLE = 3.0
    LIVENESS_INTERVAL = 5.0

    # Single synthetic pending-request key: hybrid pending prompts are
    # answered inside the TUI; TwiCC only badges their existence.
    PENDING_MARKER_KEY = "hybrid-terminal"

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        agent_settings: Any,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings)
        self.agent_pid: int | None = None
        self._untrusted = False
        self._first_paste_task: asyncio.Task[None] | None = None
        self._liveness_task: asyncio.Task[None] | None = None
        # Read by ClaudeCodeAgentManager._on_state_change for every Claude
        # Code agent (old-ProcessRun purge at first USER_TURN).
        self._old_runs_purged = False

    # ------------------------------------------------------------------
    # Manager-contract shims (SDK-agent methods reached on every Claude
    # Code agent regardless of flavor)
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        return self.agent_pid

    def get_expired_recurring_crons(self) -> list:
        # Crons on hybrid sessions are out of scope (V1): the manager's cron
        # expiry monitor polls every USER_TURN agent, so answer "none".
        return []

    async def discard_active_tool(self, tool_use_id: str) -> bool:
        # No live active-tools feed in hybrid mode (no PreToolUse hook in V1).
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool = True,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create the tmux session and schedule the first paste.

        IMPORTANT: this is awaited under the MANAGER-WIDE lock
        (``_register_and_start``) — it must return fast. Only the tmux
        creation happens inline; the first paste (with its TUI warm-up
        delay and possible trust-dialog wait) runs in a background task.
        """
        self._state_change_callback = on_state_change

        # The JSONL appears only when the first message is submitted; make
        # sure the watcher picks it up quickly (same as the SDK agent).
        from twicc.providers.claude_code.sessions_watcher import get_watcher
        get_watcher().request_fast_poll()

        # Trust clamp (security floor, trust design §13.4) — identical to the
        # SDK agent: resolve the project's effective trust and clamp the
        # permission mode before it reaches the CLI flags.
        def _resolve_trust_clamp() -> tuple[bool, str]:
            from twicc.core.services.trust import (
                clamp_permission_mode_for_untrusted,
                project_is_untrusted,
            )

            untrusted = project_is_untrusted(self.project_id)
            mode = self.agent_settings.permission_mode
            if untrusted:
                mode = clamp_permission_mode_for_untrusted(Provider.CLAUDE_CODE, mode)
            return untrusted, mode

        self._untrusted, clamped_mode = await sync_to_async(_resolve_trust_clamp)()
        if clamped_mode != self.agent_settings.permission_mode:
            self.agent_settings = self.agent_settings._replace(permission_mode=clamped_mode)

        addendum = await sync_to_async(self._read_system_prompt_addendum)()
        addendum_path = await asyncio.to_thread(
            write_addendum_file, self.session_id, addendum,
        )
        temp_title = await self._resolve_temp_title(text)
        # Same work dirs as the SDK agent (artifacts/scratch, shared
        # orchestration scratch) + the hybrid dir (attachments) — all granted
        # prompt-free via --add-dir.
        work_dirs = await self._resolve_and_create_work_dirs()
        hybrid_dir = await asyncio.to_thread(get_session_hybrid_dir, self.session_id)
        argv = build_argv(
            session_id=self.session_id,
            settings=self.agent_settings,
            resume=resume,
            temp_title=temp_title,
            addendum_path=addendum_path,
            add_dirs=[*work_dirs, str(hybrid_dir)],
            untrusted=self._untrusted,
        )

        def _create() -> tuple[int | None, bool]:
            # A leftover tmux session here can only hold a DEAD pane (live
            # ones are adopted at boot, not restarted): clear it so
            # new-session does not fail on the name collision.
            if hybrid_tmux.session_exists(self.session_id):
                hybrid_tmux.kill_session(self.session_id)
            hybrid_tmux.create_session(self.session_id, self.cwd, argv)
            return hybrid_tmux.pane_status(self.session_id)

        self.agent_pid, _ = await asyncio.to_thread(_create)
        logger.info(
            "Hybrid CLI launched for session %s (pid=%s, resume=%s)",
            self.session_id, self.agent_pid, resume,
        )
        self._first_paste_task = asyncio.create_task(
            self._first_paste(text, images, documents),
            name=f"hybrid-first-paste-{self.session_id}",
        )
        self._start_liveness_monitor()

    async def _first_paste(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> None:
        """Background task: wait for the TUI (and any trust dialog), then paste."""
        try:
            full_text = await self._materialize_attachments(text, images, documents)
            await asyncio.sleep(self.FIRST_PASTE_DELAY)
            await self._wait_for_trust_dialog_clearance()
            if self.state == AgentState.DEAD:
                return
            await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, full_text)
            if self.state == AgentState.DEAD:
                return
            # Optimistic transition; the JSONL bridge corrects it within ms
            # if the submit did not take.
            self._set_state(AgentState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "First paste failed for hybrid session %s", self.session_id,
            )
            if self.state != AgentState.DEAD:
                self.error = f"First paste failed: {e}"
                self.kill_reason = "startup-failed"
                await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
                await self._transition_to_dead()

    async def _wait_for_trust_dialog_clearance(self) -> None:
        """Block while the TUI trust dialog is on screen.

        The dialog swallows pasted text (verified), so the paste must wait
        for the user to answer it in the embedded terminal. Bounded wait;
        on timeout we paste anyway (worst case the paste is lost and the
        user re-sends — better than text silently never arriving while the
        agent looks alive forever).
        """
        deadline = time.monotonic() + self.TRUST_DIALOG_TIMEOUT
        seen_dialog = False
        while time.monotonic() < deadline:
            if self.state == AgentState.DEAD:
                return
            screen = await asyncio.to_thread(hybrid_tmux.capture_pane, self.session_id)
            if screen is None:
                # Session gone; the paste below will fail and be handled.
                return
            if not _TRUST_DIALOG_RE.search(screen):
                if seen_dialog:
                    # Let the post-dialog redraw settle before pasting.
                    await asyncio.sleep(self.TRUST_DIALOG_SETTLE)
                return
            if not seen_dialog:
                seen_dialog = True
                logger.info(
                    "Trust dialog detected for hybrid session %s — waiting for "
                    "the user to answer it in the terminal",
                    self.session_id,
                )
            await asyncio.sleep(self.TRUST_DIALOG_POLL)
        logger.warning(
            "Trust dialog still up after %.0fs for hybrid session %s — pasting anyway",
            self.TRUST_DIALOG_TIMEOUT, self.session_id,
        )

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        full_text = await self._materialize_attachments(text, images, documents)
        await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, full_text)
        self.last_activity = time.time()

    async def _materialize_attachments(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> str:
        """Save attachments to the hybrid dir and prepend ``@path`` mentions."""
        if not images and not documents:
            return text

        import mimetypes
        import secrets

        def _write_all() -> list[str]:
            hybrid_dir = get_session_hybrid_dir(self.session_id)
            paths: list[str] = []
            for block in [*(images or []), *(documents or [])]:
                source = block.get("source") or {}
                if source.get("type") != "base64" or not source.get("data"):
                    logger.warning(
                        "Skipping non-base64 attachment for hybrid session %s",
                        self.session_id,
                    )
                    continue
                import base64

                media_type = source.get("media_type") or "application/octet-stream"
                ext = mimetypes.guess_extension(media_type) or ".bin"
                # Randomized name on purpose (verified pitfall: the model can
                # answer from a meaningful filename without reading the file).
                path = hybrid_dir / f"att_{secrets.token_hex(6)}{ext}"
                path.write_bytes(base64.b64decode(source["data"]))
                paths.append(str(path))
            return paths

        paths = await asyncio.to_thread(_write_all)
        if not paths:
            return text
        mentions = "\n".join(f"@{p}" for p in paths)
        return f"{mentions}\n{text}"

    # ------------------------------------------------------------------
    # Signal-driven transitions (hooks watcher + JSONL bridge, via manager)
    # ------------------------------------------------------------------

    async def on_permission_request(self, payload: dict) -> None:
        """The single injected hook fired: a TUI prompt is up."""
        self._mark_pending_in_terminal(payload)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_user_message(self) -> None:
        """A real user prompt landed in the JSONL → a turn is running."""
        self._clear_pending_marker()
        if self.state != AgentState.ASSISTANT_TURN:
            self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_progress(self) -> None:
        """New tool_result lines landed.

        The PermissionRequest payload carries no tool_use_id (verified), so
        clearing is unconditional: any tool_result written AFTER the marker
        was set proves the prompt was answered (approve → result, deny →
        error result).
        """
        self.last_activity = time.time()
        if self._has_pending_marker():
            self._clear_pending_marker()
            await self._notify_state_change()

    async def on_jsonl_turn_end(self) -> None:
        """A ``turn_duration`` system line landed → the turn is over."""
        self._clear_pending_marker()
        if self.state != AgentState.USER_TURN:
            self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # Pending-in-terminal marker
    # ------------------------------------------------------------------

    def _mark_pending_in_terminal(self, payload: dict) -> None:
        self._pending_requests[self.PENDING_MARKER_KEY] = PendingRequest(
            request_id=self.PENDING_MARKER_KEY,
            request_type="hybrid_terminal",
            tool_name=payload.get("tool_name") or "",
            tool_input=payload.get("tool_input") or {},
            created_at=time.time(),
            permission_suggestions=payload.get("permission_suggestions") or None,
        )

    def _has_pending_marker(self) -> bool:
        return self.PENDING_MARKER_KEY in self._pending_requests

    def _clear_pending_marker(self) -> None:
        removed = self._pending_requests.pop(self.PENDING_MARKER_KEY, None)
        if removed is not None and not self._pending_requests:
            # Same stamp as _await_pending_request's finally: the time spent
            # waiting on the user must not count against the turn timeouts.
            self.last_pending_resolved_at = time.time()

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    def _start_liveness_monitor(self) -> None:
        if self._liveness_task is not None and not self._liveness_task.done():
            return
        self._liveness_task = asyncio.create_task(
            self._liveness_loop(),
            name=f"hybrid-liveness-{self.session_id}",
        )

    async def _liveness_loop(self) -> None:
        """Poll the tmux pane; transition to DEAD when claude exits.

        This is the ONLY death-detection path (no SessionEnd hook in V1's
        minimal hook set).
        """
        while True:
            await asyncio.sleep(self.LIVENESS_INTERVAL)
            if self.state == AgentState.DEAD:
                return
            try:
                pid, dead = await asyncio.to_thread(
                    hybrid_tmux.pane_status, self.session_id,
                )
            except Exception:
                logger.exception(
                    "Liveness check failed for hybrid session %s", self.session_id,
                )
                continue
            if pid is not None:
                self.agent_pid = pid
            if dead or pid is None:
                if self.state == AgentState.DEAD:
                    return
                logger.info(
                    "Hybrid CLI exited for session %s (pane_dead=%s)",
                    self.session_id, dead,
                )
                self.kill_reason = self.kill_reason or "cli-exit"
                self._clear_pending_marker()
                if self._first_paste_task is not None:
                    self._first_paste_task.cancel()
                # Remove the dead-pane tmux session so the next send can
                # recreate one under the same name.
                await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
                await self._transition_to_dead()
                return

    # ------------------------------------------------------------------
    # Kill / interrupt
    # ------------------------------------------------------------------

    async def kill(self, reason: str = "manual") -> None:
        if self.state == AgentState.DEAD:
            return
        self.kill_reason = reason
        if self._first_paste_task is not None:
            self._first_paste_task.cancel()
        if self._liveness_task is not None:
            self._liveness_task.cancel()
        self._clear_pending_marker()
        await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
        await self._transition_to_dead()

    async def interrupt_or_kill(self, reason: str) -> None:
        # Timeouts and manual stops both kill: the tmux session must go away
        # to free claude's memory. Mid-turn interruption without killing
        # stays possible by pressing Escape inside the embedded terminal
        # (V1 decision — no dedicated TwiCC affordance).
        await self.kill(reason)

    # ------------------------------------------------------------------
    # Settings / title application
    # ------------------------------------------------------------------

    async def rename(self, title: str) -> None:
        """Paste ``/rename <title>`` (verified: instant, no dialog, writes
        custom-title JSONL lines)."""
        clean = " ".join(title.split())
        if not clean:
            return
        await asyncio.to_thread(
            hybrid_tmux.paste_text, self.session_id, f"/rename {clean}",
        )

    async def apply_live_settings(self, settings: Any) -> None:
        """IDLE application: model/context via a pasted ``/model`` command.

        Hybrid categories keep everything else STARTUP (the manager kills
        and relaunches for those), so model is the only live-appliable
        field. The ``/model`` side effect of saving the user's global
        default is accepted: TwiCC re-passes the model at every launch.
        """
        from twicc.providers.helpers import get_provider_helpers

        helpers = get_provider_helpers(Provider.CLAUDE_CODE)
        old_model = helpers.resolve_sdk_model(
            self.agent_settings.selected_model, self.agent_settings.context_max,
        )
        new_model = helpers.resolve_sdk_model(
            settings.selected_model, settings.context_max,
        )
        if new_model and new_model != old_model:
            await asyncio.to_thread(
                hybrid_tmux.paste_text, self.session_id, f"/model {new_model}",
            )
            logger.info(
                "Applied /model %s to hybrid session %s", new_model, self.session_id,
            )
        self.agent_settings = settings

    # ------------------------------------------------------------------
    # Launch-time reads
    # ------------------------------------------------------------------

    def _read_system_prompt_addendum(self) -> str | None:
        """Same dual read as the SDK agent: Session row, else pending buffer."""
        from twicc.core.models import Session
        from twicc.pending_session_attributes import get_pending_session_attributes

        row = (
            Session.objects
            .filter(id=self.session_id)
            .only("system_prompt_addendum")
            .first()
        )
        if row is not None:
            return row.system_prompt_addendum
        pending = get_pending_session_attributes(self.session_id)
        return pending.system_prompt_addendum if pending else None

    async def _resolve_temp_title(self, text: str) -> str:
        """Title for ``-n``: pending/stored title, else the prompt prefix.

        Passing ``-n`` at every launch permanently suppresses the CLI's own
        ai-title generation (verified), so TwiCC's title pipeline stays
        authoritative.
        """
        from twicc.pending_titles import get_pending_title

        title = get_pending_title(self.session_id)
        if not title:
            from twicc.core.models import Session

            title = await sync_to_async(
                lambda: Session.objects.filter(id=self.session_id)
                .values_list("title", flat=True).first()
            )()
        if not title:
            title = " ".join(text.split())
        return title[:100]
