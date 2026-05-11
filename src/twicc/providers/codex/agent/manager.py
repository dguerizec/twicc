"""
Codex agent manager: tracks active Codex agents and creates new ones.

Minimal v1: no live settings (Codex doesn't expose a hot path for permission
or model changes on a running thread the way Claude Code does), no
subagents, no images/documents. Approvals are bypassed at the server level
via ``sandbox=danger_full_access`` and ``approval_policy="never"``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import codex_app_server
from codex_app_server import (
    AppServerConfig,
    AskForApproval,
    AsyncCodex,
    SandboxMode,
)

from twicc.agent import AgentState, BaseAgentManager
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from .agent import CodexAgent

logger = logging.getLogger(__name__)


def _resolve_bundled_codex_bin() -> Path:
    """Return the path to the codex binary shipped inside our wheel.

    The build hook (``hatch_build.py``) drops the platform-matching binary at
    ``codex_app_server/_bundled/{codex,codex.exe}``. Editable installs can
    populate it via ``python hatch_build.py`` if the hook didn't run.
    """
    bundled_dir = Path(codex_app_server.__file__).resolve().parent / "_bundled"
    bin_name = "codex.exe" if sys.platform == "win32" else "codex"
    bin_path = bundled_dir / bin_name
    if not bin_path.is_file():
        raise FileNotFoundError(
            f"Bundled Codex binary not found at {bin_path}. Did the build "
            "hook run? See hatch_build.py for the install/build path, or run "
            "`python hatch_build.py` to populate it in an editable install.",
        )
    return bin_path


class CodexAgentManager(BaseAgentManager):
    """Manages all active Codex agents.

    Mirrors the public surface of :class:`ClaudeCodeAgentManager` (the
    methods ``asgi._handle_send_message`` calls into) but stays much
    simpler because Codex offers no live setting controls and no
    subagent concept in this v1.

    Typical usage::

        manager = CodexAgentManager()

        # Send a message to an existing session
        await manager.send_to_session(session_id, project_id, cwd, "Hi", settings=...)

        # Create a new session (Codex mints its own canonical id)
        await manager.create_session(draft_id, project_id, cwd, "Hi", settings=...)
    """

    # ------------------------------------------------------------------
    # Public API (Codex-specific signatures)
    # ------------------------------------------------------------------

    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Send a message to an existing session.

        Routes based on the live agent's state:

        - No agent (or DEAD agent): resume the canonical thread.
        - ``USER_TURN``: schedule a new turn via ``agent.send``.
        - ``ASSISTANT_TURN``: refuse with ``RuntimeError`` — Codex doesn't
          accept mid-turn input in this v1.
        - ``STARTING``: same — safety net only; in practice the state has
          flipped to ``ASSISTANT_TURN`` by the time ``_start_agent`` returns.

        Images / documents are accepted for signature parity with
        :class:`ClaudeCodeAgentManager.send_to_session`, but ignored in v1.
        """
        async with self._lock:
            if session_id in self._agents:
                agent = self._agents[session_id]

                if agent.state == AgentState.DEAD:
                    logger.debug(
                        "Removing dead agent for session %s before resume",
                        session_id,
                    )
                    del self._agents[session_id]

                elif agent.state == AgentState.USER_TURN:
                    if not text:
                        # Settings-only update: Codex has no live settings to
                        # apply on a running thread (v1), so this is a no-op.
                        return
                    await agent.send(text)
                    return

                elif agent.state == AgentState.ASSISTANT_TURN:
                    raise RuntimeError(
                        "Cannot send message: agent is busy "
                        "(assistant turn in progress)",
                    )

                else:
                    raise RuntimeError(
                        f"Cannot send message: agent is in state {agent.state}",
                    )

            # No live agent — text is required to spin one up.
            if not text:
                raise RuntimeError("Cannot start a new agent without a message")

            await self._start_agent(
                session_id, project_id, cwd, text, resume=True,
                settings=settings,
            )

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create a brand-new Codex thread for the draft ``session_id``.

        Codex mints its own canonical thread id, so ``session_id`` here is
        the frontend-side draft. ``_create_agent`` builds the agent with the
        canonical id returned by ``thread_start``, and the base
        ``_start_agent`` broadcasts a ``session_bound`` event so the
        frontend can reconcile its local draft state.
        """
        async with self._lock:
            # No "session already exists" guard: by construction the draft id
            # is fresh per attempt; even if the frontend reuses one, Codex
            # mints a new canonical id and the (now-orphan) draft-keyed entry
            # is harmless — it will be GCed by its own DEAD transition.
            await self._start_agent(
                session_id, project_id, cwd, text, resume=False,
                settings=settings,
            )

    # ------------------------------------------------------------------
    # Factory hook
    # ------------------------------------------------------------------

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        **kwargs: Any,
    ) -> CodexAgent:
        """Spin up the AsyncCodex client + thread, wrap them in a CodexAgent.

        For ``resume=True`` the input ``session_id`` is the canonical thread
        id (the frontend already knows it). For ``resume=False`` it's the
        frontend-side draft id — Codex doesn't accept it, so we let
        ``thread_start`` mint a fresh canonical id and use that.

        Approvals are bypassed at the server level for v1:
        ``sandbox=danger_full_access`` removes file/exec restrictions, and
        ``approval_policy="never"`` tells the server not to ask. Combined,
        the default sync approval_handler in ``AppServerClient`` (which
        accepts cmd+file approvals automatically) should never be reached;
        the residual risk is an exotic approval type that falls into
        ``return {}`` — accepted for v1.

        The agent_settings bundle is stored on the agent for
        :attr:`BaseAgent.agent_settings` contract compliance but otherwise
        ignored: the model is read from ``settings.selected_model`` and
        resolved through the helpers; the rest of the live/idle/startup
        categories carry no hot-applicable values in this v1.
        """
        bundled_bin = _resolve_bundled_codex_bin()
        config = AppServerConfig(codex_bin=str(bundled_bin), cwd=cwd)
        codex = AsyncCodex(config=config)

        # ``thread_start`` / ``thread_resume`` lazy-init the transport via
        # ``_ensure_initialized`` on first call — no need to start it
        # explicitly. If anything between here and a successful thread call
        # raises, we close the transport so we don't leak the codex
        # subprocess.
        try:
            approval_policy = AskForApproval.model_validate("never")
            sandbox = SandboxMode.danger_full_access
            if resume:
                # Model is sticky to the existing thread server-side — leave it
                # unset so the resumed thread keeps whatever model it was started
                # with. Sandbox / approval are re-asserted because the SDK
                # contract requires resume to be self-contained.
                thread = await codex.thread_resume(
                    session_id,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                )
            else:
                # Resolve the user's selected_model alias (e.g. "gpt",
                # "gpt-5.4", "gpt-mini") to the SDK full name the Codex CLI
                # expects (e.g. "gpt-5.5", "gpt-5.4", "gpt-5.4-mini").
                # Falls back to ``None`` for an empty input — Codex CLI then
                # picks its own default.
                helpers = get_provider_helpers(Provider.CODEX)
                sdk_model = helpers.resolve_sdk_model(settings.selected_model)
                thread = await codex.thread_start(
                    model=sdk_model,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                )
        except Exception:
            try:
                await codex.close()
            except Exception:
                logger.debug(
                    "codex.close() failed while unwinding _create_agent",
                    exc_info=True,
                )
            raise

        # On resume ``thread.id == session_id``; on new sessions Codex picked
        # its own canonical id and ``_start_agent`` will broadcast the
        # ``session_bound`` mapping (draft → canonical) for the frontend.
        return CodexAgent(
            session_id=thread.id,
            project_id=project_id,
            cwd=cwd,
            settings=settings,
            codex=codex,
            thread=thread,
        )


def get_codex_agent_manager() -> CodexAgentManager:
    """Return the Codex agent manager singleton from the registry.

    Convenience wrapper for Codex-specific call sites (orchestrator
    shutdown). The actual instance is owned by the global
    :class:`AgentManagerRegistry`.
    """
    # Lazy import to avoid an import cycle: the registry imports this module.
    from twicc.agent.registry import get_agent_manager_registry

    manager = get_agent_manager_registry().get(Provider.CODEX)
    return manager  # type: ignore[return-value]
