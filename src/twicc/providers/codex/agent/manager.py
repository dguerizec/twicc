"""
Codex agent manager: tracks active Codex agents and creates new ones.

Minimal v1: no live settings (Codex doesn't expose a hot path for permission
or model changes on a running thread the way Claude Code does) and no
subagents. Images are forwarded to the Codex SDK as ``ImageInput`` data
URLs; documents (PDF / TXT) have no Codex protocol equivalent and are
silently dropped with a warning. Approvals are routed through ``CodexAgent``'s
sync ↔ async bridge to the shared ``PendingRequest`` plumbing; the sandbox +
approval policy come from the user's ``permission_mode`` preset via
:func:`resolve_codex_policy` (see ``permission_modes.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from codex_app_server import (
    AppServerConfig,
    AsyncCodex,
)

from twicc.agent import AgentState, BaseAgent, BaseAgentManager
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from ..bin import resolve_bundled_binary
from ..permission_modes import resolve_codex_policy
from .agent import CodexAgent

logger = logging.getLogger(__name__)


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

    # No ``_on_state_change`` override: the pending-title flush is handled
    # by :meth:`BaseAgentManager._flush_pending_title`, which delegates to
    # :meth:`CodexHelpers.rename_session` for the Codex-specific
    # ``thread/name/set`` SDK call. Nothing else needed at state-change
    # time on this provider.

    # ------------------------------------------------------------------
    # Public API (Codex-specific signatures)
    # ------------------------------------------------------------------

    @staticmethod
    def _warn_about_documents(session_id: str, documents: list[dict] | None) -> None:
        """Defensive log when the frontend ships ``documents`` to a Codex session.

        The frontend's :class:`CodexHelpers.getAttachmentSupport` declares
        ``documents: false`` and refuses them at the file picker / paste /
        drop layer, so reaching this point means either a UI bug or a
        custom WebSocket client. Either way, Codex has no protocol for
        PDF / TXT input, so we drop them and surface the discrepancy to
        the logs rather than failing the whole turn.
        """
        if documents:
            logger.warning(
                "Codex session %s received %d document attachment(s); "
                "Codex has no protocol for documents — dropping them silently.",
                session_id, len(documents),
            )

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
        - ``ASSISTANT_TURN``: steer the active turn — refresh the agent
          settings bundle for the next ``_run_turn`` and forward to
          ``agent.send``, which routes to ``turn_handle.steer``.
        - ``STARTING``: refuse — safety net only; in practice the state has
          flipped to ``ASSISTANT_TURN`` by the time ``_start_agent`` returns.

        ``images`` are forwarded to the SDK as ``ImageInput`` data URLs;
        ``documents`` are dropped with a warning (Codex protocol has no
        equivalent input block).
        """
        self._warn_about_documents(session_id, documents)

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
                    if not text and not images:
                        # Settings-only update with no text/images to send: nothing
                        # triggers a new turn, so the agent_settings bundle on the
                        # ``CodexAgent`` won't be re-read by ``_run_turn`` until the
                        # user actually sends something. We could mirror the new
                        # settings onto the agent here, but a stale bundle is harmless
                        # as long as the next ``send_to_session`` refreshes it
                        # (which it does).
                        return
                    # Refresh the bundle on the live agent so the upcoming turn picks
                    # up any field changed since creation. ``CodexAgent._run_turn``
                    # reads ``effort``, ``permission_mode`` and ``selected_model`` off
                    # ``agent_settings`` on every ``thread.turn`` call, so changing
                    # the picker mid-session takes effect on the NEXT turn (this one).
                    old_settings = agent.agent_settings
                    logger.debug(
                        "Codex live settings update: session=%s "
                        "permission_mode=%r->%r effort=%r->%r "
                        "selected_model=%r->%r",
                        session_id,
                        old_settings.permission_mode, settings.permission_mode,
                        old_settings.effort, settings.effort,
                        old_settings.selected_model, settings.selected_model,
                    )
                    agent.agent_settings = settings
                    await agent.send(text, images=images)
                    return

                elif agent.state == AgentState.ASSISTANT_TURN:
                    if not text and not images:
                        # Settings-only update during an active turn. Refresh
                        # the bundle so the NEXT turn picks up the new picker
                        # values; nothing to steer.
                        agent.agent_settings = settings
                        return
                    # Steer: refresh the bundle (the active turn keeps the
                    # policy it was started with — ``turn/steer`` has no
                    # override knobs, the new values land on the next
                    # ``_run_turn``), then forward to ``agent.send`` which
                    # detects ``ASSISTANT_TURN`` and routes to
                    # ``turn_handle.steer`` instead of scheduling a new turn.
                    old_settings = agent.agent_settings
                    logger.debug(
                        "Codex live settings update during active turn (steer): "
                        "session=%s permission_mode=%r->%r effort=%r->%r "
                        "selected_model=%r->%r",
                        session_id,
                        old_settings.permission_mode, settings.permission_mode,
                        old_settings.effort, settings.effort,
                        old_settings.selected_model, settings.selected_model,
                    )
                    agent.agent_settings = settings
                    await agent.send(text, images=images)
                    return

                else:
                    raise RuntimeError(
                        f"Cannot send message: agent is in state {agent.state}",
                    )

            # No live agent — text or at least one image is required to
            # spin one up.
            if not text and not images:
                raise RuntimeError("Cannot start a new agent without a message")

            await self._start_agent(
                session_id, project_id, cwd, text, resume=True,
                settings=settings, images=images,
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
    ) -> str:
        """Create a brand-new Codex thread for the draft ``session_id``.

        Codex mints its own canonical thread id, so ``session_id`` here is
        the frontend-side draft. ``_create_agent`` builds the agent with the
        canonical id returned by ``thread_start``, and the base
        ``_start_agent`` broadcasts a ``session_bound`` event so the
        frontend can reconcile its local draft state.

        Returns the canonical session id minted by ``thread_start`` — this
        differs from the draft ``session_id`` parameter.

        Same image / document policy as ``send_to_session``: images go
        through; documents are warned-and-dropped.
        """
        self._warn_about_documents(session_id, documents)

        async with self._lock:
            # No "session already exists" guard: by construction the draft id
            # is fresh per attempt; even if the frontend reuses one, Codex
            # mints a new canonical id and the (now-orphan) draft-keyed entry
            # is harmless — it will be GCed by its own DEAD transition.
            return await self._start_agent(
                session_id, project_id, cwd, text, resume=False,
                settings=settings, images=images,
            )

    def get_denied_tool_reason(
        self, session_id: str, item_id: str,
    ) -> str | None:
        """Return the recorded refusal reason for ``(session_id, item_id)``, or None.

        Called by :class:`twicc.providers.codex.compute.CodexSessionCompute`
        to surface user-initiated refusals (Deny / Cancel turn / empty
        permissions) as ``ToolResultLink.error`` when the matching
        ``function_call_output`` arrives in the JSONL.

        Returns ``None`` if there is no live agent for the session (e.g.
        the agent died and was GC'd, or this is a background re-compute
        on a session from a previous backend run) or if the item_id was
        never refused.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return None
        # ``CodexAgent`` owns ``_denied_tool_ids`` — see the comment on the
        # map in ``CodexAgent.__init__``.
        reason = agent._denied_tool_ids.get(item_id)
        if reason is not None:
            # Hit-only logging: a miss is the common case (every
            # function_call_output triggers a lookup, almost none are
            # denied), so logging both branches would drown out the signal.
            logger.debug(
                "Codex denied-tool lookup hit: session=%s itemId=%s reason=%r",
                session_id, item_id, reason,
            )
        return reason

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

        Sandbox + approval policy come from the user's preset (the
        ``permission_mode`` field on the bundle), translated by
        :func:`resolve_codex_policy`. The default preset is ``"auto"``
        (``workspace-write`` + ``on-request``) so a session without an
        explicit mode emits approvals on shell/network/filesystem
        operations that step outside the workspace; users opt into
        ``"yolo"`` to recover the pre-existing unrestricted bypass.

        The agent_settings bundle is stored on the agent for
        :attr:`BaseAgent.agent_settings` contract compliance but otherwise
        ignored: the model is read from ``settings.selected_model`` and
        resolved through the helpers; the rest of the live/idle/startup
        categories carry no hot-applicable values in this v1.
        """
        bundled_bin = resolve_bundled_binary()
        config = AppServerConfig(codex_bin=str(bundled_bin), cwd=cwd)
        codex = AsyncCodex(config=config)

        # ``thread_start`` / ``thread_resume`` lazy-init the transport via
        # ``_ensure_initialized`` on first call — no need to start it
        # explicitly. If anything between here and a successful return
        # raises, we close the transport so we don't leak the codex
        # subprocess. This includes the ``CodexAgent`` constructor below:
        # it monkey-patches the private SDK path
        # ``_codex._client._sync._approval_handler`` (see
        # ``CodexAgent.__init__``), so a future SDK rename would surface as
        # ``AttributeError`` here and must NOT leak the already-spawned
        # subprocess. Once the agent is returned, ownership transfers to
        # the caller (``BaseAgentManager._start_agent`` covers the rest of
        # the startup sequence via its own cleanup wrapper).
        try:
            # Translate the user's preset (Session.permission_mode) into
            # the SDK couple. Unset / unknown modes fall on
            # ``permission_modes.DEFAULT_MODE`` (currently ``"auto"`` =
            # ``workspace-write`` + ``on-request``).
            sandbox, approval_policy = resolve_codex_policy(
                settings.permission_mode,
            )
            # Per-thread config overrides. ``config`` on thread_start /
            # thread_resume reaches the server as a fresh ``ConfigToml`` patch
            # scoped to this thread, which is more reliable than ``-c`` CLI
            # overrides (those bind at app-server boot and can be ignored by
            # the per-thread request layer). We force ``detailed`` reasoning
            # summaries so the JSONL captures the model's thinking text —
            # needed for the TwiCC "thinking" stream support we're wiring
            # next. Every model in the catalog already has
            # ``supports_reasoning_summaries=true``; the only knob that
            # actually moves the needle is the summary verbosity itself.
            thread_config: dict[str, Any] = {
                "model_reasoning_summary": "detailed",
            }
            if resume:
                # Model is sticky to the existing thread server-side — leave it
                # unset so the resumed thread keeps whatever model it was started
                # with. Sandbox / approval are re-asserted because the SDK
                # contract requires resume to be self-contained.
                thread = await codex.thread_resume(
                    session_id,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                    config=thread_config,
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
                    config=thread_config,
                )

            # On resume ``thread.id == session_id``; on new sessions Codex
            # picked its own canonical id and ``_start_agent`` will broadcast
            # the ``session_bound`` mapping (draft → canonical) for the
            # frontend.
            return CodexAgent(
                session_id=thread.id,
                project_id=project_id,
                cwd=cwd,
                settings=settings,
                codex=codex,
                thread=thread,
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

    # ------------------------------------------------------------------
    # Timeout policy
    # ------------------------------------------------------------------

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Delegate to the shared per-state policy — no Codex-specific skips.

        The ``pending_requests`` skip (load-bearing for any session in
        ``auto`` / ``read_only`` / ``autonomous`` modes — the sync ↔ async
        approval bridge in :class:`CodexAgent` populates the map) lives in
        :meth:`BaseAgentManager._state_based_timeout` and is shared with
        every provider that calls into it. No equivalent of Claude's
        ``SessionCron`` check because :class:`SessionCron` is Claude
        Code-specific.
        """
        return self._state_based_timeout(agent, current_time)


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
