"""
Claude Code agent: wraps a single SDK client instance for one TwiCC session.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate, ResultMessage, StreamEvent, SystemMessage, ThinkingConfigAdaptive, ThinkingConfigDisabled,
    ToolPermissionContext, UserMessage,
)

from channels.layers import get_channel_layer
import json_repair

from twicc.agent import AgentInfo, AgentState, BaseAgent, PendingRequest, StateChangeCallback
from twicc.core.enums import Provider
from twicc.agent.plugin import get_plugin_dir
from twicc.providers.helpers import AgentSettings

from .sdk_logger import patch_client as patch_client_for_logging
from .tool_label_filter import filter_tool_input

logger = logging.getLogger(__name__)


def _capture_original_file(input_data: dict, tool_use_id: str) -> None:
    """Read and cache a file's content before it gets modified by Edit/Write."""
    from twicc.providers.claude_code.agent.original_file_cache import MAX_FILE_SIZE, cache_original_file

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    try:
        path = Path(file_path)
        if not path.is_file():
            return
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            return
        content = path.read_text(encoding="utf-8")
        cache_original_file(session_id, tool_use_id, content)
    except Exception:
        logger.debug("Failed to cache original file %s for tool_use %s", file_path, tool_use_id, exc_info=True)


# Type aliases for cron change callbacks
CronCreatedCallback = Callable[
    [str, str, str, bool, str, "datetime", "datetime"],
    # session_id, cron_id, cron_expr, recurring, prompt, created_at, next_fire
    Coroutine[Any, Any, None],
]
CronDeletedCallback = Callable[
    [str, str],  # session_id, cron_id
    Coroutine[Any, Any, None],
]


class ClaudeCodeAgent(BaseAgent):
    """Claude Code SDK agent for a single TwiCC session.

    Manages the lifecycle of a Claude Code SDK client (connection, message sending,
    state tracking). Designed to be completely isolated so that any errors in
    the SDK never propagate to crash the server.
    """

    provider = Provider.CLAUDE_CODE

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        settings: AgentSettings,
        get_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
        on_cron_created: CronCreatedCallback,
        on_cron_deleted: CronDeletedCallback,
    ) -> None:
        """Initialize a Claude Code agent wrapper.

        Args:
            session_id: The session to resume
            project_id: The TwiCC project this belongs to
            cwd: Working directory for Claude operations
            settings: Per-session agent settings (already resolved against
                synced defaults). Each field is unpacked onto ``self`` so
                the rest of the agent code keeps using the individual
                attributes; mutations via ``set_permission_mode`` /
                ``apply_live_settings`` update those attributes in place.
            get_session_slug: Async callback that retrieves the slug stored on the
                Session model. Takes a session_id and returns the slug string or None.
            on_cron_created: Async callback fired when a CronCreate PostToolUse event occurs.
                Receives (session_id, cron_id, cron_expr, recurring, prompt, created_at, next_fire).
            on_cron_deleted: Async callback fired when a CronDelete PostToolUse event occurs.
                Receives (session_id, cron_id).
        """
        super().__init__(session_id, project_id, cwd, agent_settings=settings)

        self._client: ClaudeSDKClient | None = None
        self._interrupting = False  # Set when interrupt() is called, suppresses error toast
        self._message_loop_task: asyncio.Task[None] | None = None
        self._active_tools: dict[str, dict[str, Any]] = {}
        self._last_started_tool_id: str | None = None
        self._get_session_slug = get_session_slug
        self._on_cron_created = on_cron_created
        self._on_cron_deleted = on_cron_deleted

        # ProcessRun lifecycle tracking
        self._first_turn_done_event = asyncio.Event()  # Set on first USER_TURN or DEAD
        self._first_user_turn_reached = False           # True only if USER_TURN was reached (not DEAD)
        self._old_runs_purged = False                   # True after old ProcessRuns are purged at first USER_TURN

        logger.debug(
            "ClaudeCodeAgent created for session %s, project %s, cwd=%s, settings=%s",
            session_id,
            project_id,
            cwd,
            settings,
        )

    def _log_stderr(self, line: str) -> None:
        """Log stderr output from the Claude Code CLI subprocess.

        This callback is passed to the SDK to capture stderr lines.
        """
        logger.warning(
            "Claude Code stderr for session %s: %s",
            self.session_id,
            line.rstrip(),
        )

    def get_pid(self) -> int | None:
        """Get the PID of the underlying Claude Code CLI subprocess.

        This accesses internal SDK attributes to extract the subprocess PID.
        May return None if the process is not started or has terminated.

        Returns:
            Process ID of the Claude Code CLI subprocess, or None if unavailable
        """
        try:
            if self._client is None:
                return None
            # Access internal SDK attributes: ClaudeSDKClient._transport._process.pid
            transport = getattr(self._client, "_transport", None)
            if transport is None:
                return None
            process = getattr(transport, "_process", None)
            if process is None:
                return None
            return getattr(process, "pid", None)
        except Exception:
            return None

    def get_expired_recurring_crons(self) -> list:
        """Return recurring SessionCron instances whose CLI auto-delete has occurred.

        Checks each recurring cron's expired_at (last_fire + jitter + margin) against
        the current time. Non-recurring crons are excluded.

        This is a synchronous method (DB access) — call via asyncio.to_thread.

        Returns:
            List of SessionCron instances that have expired.
        """
        from twicc.core.models import SessionCron

        now = datetime.now(tz=timezone.utc)
        expired = []
        for cron in SessionCron.objects.filter(session_id=self.session_id, recurring=True):
            if cron.expired_at is not None and now >= cron.expired_at:
                expired.append(cron)
        return expired

    async def _handle_cron_tool_event(self, input_data: dict) -> None:
        """Process a CronCreate or CronDelete PostToolUse event.

        Parses the SDK hook data and delegates to the on_cron_created / on_cron_deleted
        callbacks for DB persistence. Called from the PostToolUse hook closure in start().

        For CronCreate, the next fire time is always computed via cronsim. If the
        expression cannot be parsed, or the computed fire time is already in the past
        for a one-shot cron, the cron is not persisted (it would never fire).

        Args:
            input_data: The PostToolUseHookInput dict from the SDK, containing
                tool_name, tool_input, and tool_response.
        """
        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})
        tool_response = input_data.get("tool_response")

        # logger.debug(f"CronCreate called with input = {tool_input} and response = {tool_response}")

        if tool_name == "CronCreate" and isinstance(tool_response, dict):
            job_id = tool_response.get("id")
            if not job_id:
                return

            cron_expr = tool_input.get("cron", "")
            recurring = tool_response.get("recurring", True)
            prompt = tool_input.get("prompt", "")

            from datetime import datetime as dt, timezone as tz
            from twicc.core.models import cron_occurrences

            created_at = dt.now(tz=tz.utc)

            # Compute next fire time from the cron expression (always required)
            try:
                it = cron_occurrences(cron_expr, created_at)
                next_fire = next(it)
            except Exception:
                logger.warning(
                    "Failed to parse cron expression '%s' for session %s, skipping persistence",
                    cron_expr,
                    self.session_id,
                )
                return

            # For one-shot crons, skip if next_fire is already in the past
            if not recurring and next_fire <= created_at:
                logger.warning(
                    "One-shot cron '%s' for session %s has next_fire in the past (%s), skipping persistence",
                    cron_expr,
                    self.session_id,
                    next_fire,
                )
                return

            logger.info(
                "Cron job created for session %s: id=%s, cron=%s, recurring=%s, next_fire=%s",
                self.session_id,
                job_id,
                cron_expr,
                recurring,
                next_fire,
            )

            try:
                await self._on_cron_created(
                    self.session_id, job_id, cron_expr, recurring,
                    prompt, created_at, next_fire,
                )
            except Exception as e:
                logger.error("Error in on_cron_created callback for session %s: %s", self.session_id, e)

        elif tool_name == "CronDelete" and isinstance(tool_response, dict):
            job_id = tool_response.get("id") or tool_input.get("id")
            if not job_id:
                return

            logger.info("Cron job deleted for session %s: id=%s", self.session_id, job_id)

            try:
                await self._on_cron_deleted(self.session_id, job_id)
            except Exception as e:
                logger.error("Error in on_cron_deleted callback for session %s: %s", self.session_id, e)

    def _serialize_active_tools(self) -> list[dict]:
        """Return ``_active_tools`` as a list of dicts ready for transport."""
        return [
            {
                "id": tool_use_id,
                "name": entry["name"],
                "input": entry["input"],
                "streaming": entry.get("streaming", False),
            }
            for tool_use_id, entry in self._active_tools.items()
        ]

    def get_info(self) -> AgentInfo:
        """Get an immutable snapshot of the agent state.

        Extends the base snapshot with Claude-specific fields (active tools
        and the most recently started tool id). ``pending_requests`` is
        populated by the base implementation.
        """
        return super().get_info()._replace(
            active_tools=tuple(self._serialize_active_tools()),
            last_started_tool_id=self._last_started_tool_id,
        )

    async def discard_active_tool(self, tool_use_id: str) -> bool:
        """Remove an active-tool entry by id and broadcast if it existed.

        Used by the JSONL watcher as a safety net: PostToolUse/PostToolUseFailure
        do not fire when the CLI rejects a tool call before execution (e.g.
        Edit/Write on a not-yet-read file — the CLI synthesises an is_error
        tool_result without ever invoking the tool). Without this hook, those
        entries would leak into _active_tools and persist in the working
        status line.
        """
        if tool_use_id not in self._active_tools:
            return False
        self._active_tools.pop(tool_use_id, None)
        # logger.debug(
        #     "discard_active_tool session=%s tool=%s active_tools=%d",
        #     self.session_id,
        #     entry["name"] if entry else "?",
        #     len(self._active_tools),
        # )
        await self._broadcast_process_tools()
        return True

    def get_permission_suggestions(
        self, tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> list[dict] | None:
        """Extract, serialize and filter permission suggestions from the SDK context.

        The SDK may provide suggestions as ``PermissionUpdate`` dataclass instances
        or, in some cases, as plain dicts. This method normalises both forms into
        a list of JSON-serialisable dicts ready for transmission to the frontend.

        Filtering applied:
        - For ``addDirectories`` / ``removeDirectories`` suggestions, the project
          directory (``self.cwd``) is removed from the directories list (it is always
          implicitly allowed). If the directories list becomes empty after filtering,
          the suggestion is dropped entirely.

        Args:
            tool_name: The name of the tool requesting permission (used to generate
                MCP suggestions when the SDK provides none)
            input_data: The tool's input parameters (reserved for future filtering)
            context: SDK ``ToolPermissionContext`` containing the suggestions list

        Returns:
            A list of serialised permission suggestion dicts, or ``None`` if there
            are none (or all were filtered out).
        """
        serialized = [s.to_dict() if isinstance(s, PermissionUpdate) else s for s in context.suggestions or ()]

        result = []
        for suggestion in serialized:
            suggestion_type = suggestion.get("type")

            # Drop any setMode suggestion from the SDK — we inject our own at the end
            # so the user always sees the full set of mode options regardless of context.
            if suggestion_type == "setMode":
                continue

            # Strip the project directory from directory suggestions (always implicitly allowed).
            if suggestion_type in ("addDirectories", "removeDirectories"):
                directories = suggestion.get("directories")
                if directories is not None:
                    # Filter out the project directory — it is always implicitly allowed
                    directories = [d for d in directories if d != self.cwd]
                    if not directories:
                        # Nothing left to suggest after filtering
                        continue
                    suggestion = {**suggestion, "directories": directories}

            # Ungroup suggestions that bundle multiple rules: split into one
            # suggestion per rule so the frontend can present them individually.
            rules = suggestion.get("rules")
            if rules and len(rules) > 1:
                for rule in rules:
                    result.append({**suggestion, "rules": [rule]})
            else:
                result.append(suggestion)

        # Inject wildcard MCP suggestions: for each rule targeting a specific MCP tool
        # (mcp__{name}__{tool}), add a wildcard suggestion (mcp__{name}__*) so the user
        # can choose to allow all tools from that MCP server at once.
        # Collect all existing toolNames to know what's already covered.
        existing_tool_names: set[str] = set()
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                if tn:
                    existing_tool_names.add(tn)

        # If no suggestion covers the current tool and it's an MCP tool, create one.
        # The wildcard loop below will then automatically add the server-wide variant.
        if tool_name not in existing_tool_names:
            parts = tool_name.split("__")
            if len(parts) == 3 and parts[0] == "mcp":
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': tool_name}],
                    'behavior': 'allow',
                    'destination': 'localSettings',
                })
                existing_tool_names.add(tool_name)

        # If the current tool is WebFetch, ensure both a domain-specific and a global suggestion exist.
        # Domain-specific: ruleContent = "domain:<host>" extracted from the URL in input_data.
        # Global: no ruleContent, allows WebFetch on any domain.
        if tool_name == "WebFetch":
            url = input_data.get("url")
            if url:
                host = urlparse(url if "://" in url else f"https://{url}").hostname
                if host:
                    domain_rule = f"domain:{host}"
                    has_domain = any(
                        rule.get("toolName") == "WebFetch" and rule.get("ruleContent") == domain_rule
                        for s in result for rule in s.get("rules") or ()
                    )
                    if not has_domain:
                        result.append({
                            'type': 'addRules',
                            'rules': [{'toolName': 'WebFetch', 'ruleContent': domain_rule}],
                            'behavior': 'allow',
                            'destination': 'session',
                        })
            # Global suggestion inherits the destination from the domain-specific one
            # (either from the SDK or the one we just created above).
            domain_suggestion = next(
                (s for s in result for rule in s.get("rules") or ()
                 if rule.get("toolName") == "WebFetch" and rule.get("ruleContent")),
                None,
            )
            domain_destination = domain_suggestion["destination"] if domain_suggestion else "session"
            has_global = any(
                rule.get("toolName") == "WebFetch" and not rule.get("ruleContent")
                for s in result for rule in s.get("rules") or ()
            )
            if not has_global:
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': 'WebFetch'}],
                    'behavior': 'allow',
                    'destination': domain_destination,
                })

        # If the current tool is WebSearch and no suggestion exists for it, create one.
        if tool_name == "WebSearch":
            has_web_search = any(
                rule.get("toolName") == "WebSearch"
                for s in result for rule in s.get("rules") or ()
            )
            if not has_web_search:
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': 'WebSearch'}],
                    'behavior': 'allow',
                    'destination': 'session',
                })

        # If the current tool is Read, Edit, or Write, generate a suggestion with a
        # ruleContent select covering: exact file, then for each ancestor directory
        # (from deepest to shallowest) the extension glob and the catch-all glob,
        # and finally "allow all" (no ruleContent). Write maps to Edit rules.
        if tool_name in ("Read", "Edit", "Write"):
            file_path = input_data.get("file_path")
            if file_path and file_path.startswith("/"):
                suggestion_tool = "Read" if tool_name == "Read" else "Edit"
                path = PurePosixPath(file_path)
                ext = path.suffix  # e.g. ".py"

                # Build ruleContent options: exact file first
                options = [f"//{file_path.lstrip('/')}"]

                # For each ancestor directory, add extension glob then catch-all glob
                parent = path.parent
                while parent != PurePosixPath("/"):
                    prefix = f"//{str(parent).lstrip('/')}"
                    if ext:
                        options.append(f"{prefix}/*{ext}")
                    options.append(f"{prefix}/*")
                    if ext:
                        options.append(f"{prefix}/**/*{ext}")
                    options.append(f"{prefix}/**")
                    parent = parent.parent

                # Root-level extension glob (e.g. //**/*.py) — no //** since that equals "allow all"
                if ext:
                    options.append(f"//**/*{ext}")

                # Last option: allow all (empty ruleContent rendered as tool name only)
                options.append("")

                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': suggestion_tool, 'ruleContent': options[0]}],
                    'behavior': 'allow',
                    'destination': 'session',
                    '_ruleContentOptions': options,
                })

        # For each specific MCP tool suggestion, add a server-wide wildcard variant
        # (mcp__{name}__*) so the user can allow all tools from that server at once.
        seen_mcp_prefixes: set[str] = set()
        extra: list[dict] = []
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                # Match mcp__{name}__{tool} — exactly 3 parts separated by "__"
                parts = tn.split("__")
                if len(parts) != 3 or parts[0] != "mcp":
                    continue
                mcp_prefix = f"mcp__{parts[1]}"
                if mcp_prefix in seen_mcp_prefixes:
                    continue
                seen_mcp_prefixes.add(mcp_prefix)
                wildcard = f"{mcp_prefix}__*"
                # Only add if neither the bare prefix nor the wildcard already exist
                if mcp_prefix not in existing_tool_names and wildcard not in existing_tool_names:
                    extra.append({**s, "rules": [{"toolName": wildcard}]})

        result.extend(extra)

        # Inject a setMode suggestion so the user can switch the session's permission
        # mode directly from the approval screen. Skipped for ExitPlanMode, where the
        # CLI handles the plan→default transition automatically on approval.
        if tool_name != "ExitPlanMode":
            current_mode = self.agent_settings.permission_mode
            mode_options = [m for m in ("default", "acceptEdits", "bypassPermissions") if m != current_mode]
            if mode_options:
                result.append({
                    'type': 'setMode',
                    'mode': None,
                    'destination': 'session',
                    '_modeOptions': mode_options,
                    '_currentMode': current_mode,
                })

        # Normalize field order to match PermissionUpdate dataclass definition.
        # Private fields (prefixed with _) are preserved at the end for frontend use.
        field_order = ("type", "rules", "behavior", "mode", "directories", "destination")
        result = [
            {k: s[k] for k in field_order if k in s}
            | {k: v for k, v in s.items() if k.startswith("_")}
            for s in result
        ]

        return result or None

    async def _handle_pending_request(
        self,
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK can_use_tool callback: creates a pending request and waits for resolution.

        Called by the SDK when Claude wants to use a tool that requires permission,
        or when Claude asks a clarifying question via AskUserQuestion. This method
        blocks (via an asyncio Future) until the frontend resolves the request.

        Args:
            tool_name: The tool Claude wants to use (e.g., "Bash", "AskUserQuestion")
            input_data: The tool's input parameters
            context: SDK ToolPermissionContext with optional permission suggestions

        Returns:
            PermissionResultAllow or PermissionResultDeny from the user's response
        """
        request_id = str(uuid.uuid4())

        if tool_name == "AskUserQuestion":
            request_type = "ask_user_question"
        else:
            request_type = "tool_approval"

        permission_suggestions = self.get_permission_suggestions(tool_name, input_data, context)

        request = PendingRequest(
            request_id=request_id,
            request_type=request_type,
            tool_name=tool_name,
            tool_input=input_data,
            created_at=time.time(),
            permission_suggestions=permission_suggestions,
        )

        try:
            response = await self._await_pending_request(request)
        except asyncio.CancelledError:
            logger.warning(
                "[session %s] [permission %s] Future cancelled while awaiting (tool=%r)",
                self.session_id, request_id, tool_name,
            )
            raise

        # For ExitPlanMode: detect if the user modified the plan content.
        # Because of a "bug" in claude-agent-sdk / claude-code, the plan passed
        # via the response is not taken into account, so we update it ourselves
        # in the plan file (via ``_update_plan``).
        if (
            tool_name == "ExitPlanMode"
            and isinstance(response, PermissionResultAllow)
            and response.updated_input is not None
            and response.updated_input.get("plan") != input_data.get("plan")
        ):
            await self._update_plan(response.updated_input["plan"])

        return response

    def _build_query_prompt(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> AsyncIterator[dict]:
        """Build prompt for SDK query() as an async generator.

        Always returns an async generator yielding a single transport message,
        which is required for streaming mode (needed by the can_use_tool callback).

        Each yielded dict is a complete transport message with the structure:
            {"type": "user", "message": {"role": "user", "content": [...]}, ...}

        Args:
            text: The message text
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Returns:
            An async iterator yielding a single transport message dict.
        """
        content_blocks: list[dict] = []

        if images:
            content_blocks.extend(images)

        if documents:
            content_blocks.extend(documents)

        content_blocks.append({"type": "text", "text": text})

        async def _message_stream() -> AsyncIterator[dict]:
            yield {
                "type": "user",
                "message": {"role": "user", "content": content_blocks},
                "parent_tool_use_id": None,
            }

        return _message_stream()

    async def start(
        self,
        prompt: str,
        on_state_change: StateChangeCallback,
        resume: bool = True,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Start the process and send the first message.

        This connects to Claude and sends the initial prompt. A background task
        is started to consume messages and track state changes.

        Args:
            prompt: The initial message to send
            on_state_change: Async callback invoked when process state changes
            resume: If True (default), resume an existing session. If False,
                   create a new session with the session_id as the custom UUID.
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Raises:
            RuntimeError: If the process is already started
        """
        if self._client is not None:
            raise RuntimeError("Process already started")

        self._state_change_callback = on_state_change

        logger.debug(
            "Starting process for session %s (resume=%s)", self.session_id, resume
        )

        # If ~/.claude/projects/ doesn't exist yet, the watcher is currently
        # in slow-poll mode (30s). The SDK is about to create that directory
        # for this session — boost the watcher to 5s so we pick up the new
        # JSONL file quickly.
        from twicc.providers.claude_code.sessions_watcher import get_watcher
        get_watcher().request_fast_poll()

        try:
            self._active_tools = {}
            self._last_started_tool_id = None
            # Create options - either resume existing session or create new with custom ID
            # Build thinking config from boolean flag
            thinking_config = None
            if self.agent_settings.thinking_enabled is True:
                thinking_config = ThinkingConfigAdaptive(type="adaptive", display="summarized")
            elif self.agent_settings.thinking_enabled is False:
                thinking_config = ThinkingConfigDisabled(type="disabled")

            # PostToolUse hook for cron tracking — closure captures self so the
            # hook can persist cron changes via the on_cron_created/on_cron_deleted callbacks.
            async def _on_cron_tool(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                await self._handle_cron_tool_event(input_data)
                return {"continue_": True}

            async def _pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                tool_name = input_data.get("tool_name", "") or ""
                # Subagent tools carry an "agent_id" field; the parent session must
                # ignore them so its working-status feed stays scoped to its own work.
                is_subagent_tool = "agent_id" in input_data
                if tool_name in ("Edit", "Write") and not is_subagent_tool:
                    _capture_original_file(input_data, tool_use_id)
                # Streaming registered the tool with streaming=True so the frontend
                # always shows its summary while input chunks were arriving. By the
                # time PreToolUse fires, the input is final — flip the flag off so
                # the "lone latest tool → no parens" rule can apply again.
                if tool_use_id and tool_use_id in self._active_tools:
                    entry = self._active_tools[tool_use_id]
                    if entry.get("streaming"):
                        entry["streaming"] = False
                        await self._broadcast_process_tools()
                return {"continue_": True}

            async def _post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                if tool_use_id and tool_use_id in self._active_tools:
                    self._active_tools.pop(tool_use_id, None)
                    # event = input_data.get("hook_event_name", "PostToolUse")
                    # logger.debug(
                    #     "%s session=%s tool=%s active_tools=%d",
                    #     event,
                    #     self.session_id,
                    #     entry["name"] if entry else "?",
                    #     len(self._active_tools),
                    # )
                    await self._broadcast_process_tools()
                return {"continue_": True}

            extra_args: dict[str, str | None] = {
                "allow-dangerously-skip-permissions": None,
                ("chrome" if self.agent_settings.claude_in_chrome else "no-chrome"): None
            }

            options = ClaudeAgentOptions(
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",  # Use Claude Code's system prompt
                },
                cwd=self.cwd,
                permission_mode=self.agent_settings.permission_mode,
                model=self.sdk_model,
                effort=self.agent_settings.effort,
                thinking=thinking_config,
                setting_sources=["user", "project", "local"],
                plugins=[{"type": "local", "path": str(get_plugin_dir())}],
                can_use_tool=self._handle_pending_request,
                hooks={
                    "PreToolUse": [HookMatcher(matcher=None, hooks=[_pre_tool_use])],
                    "PostToolUse": [
                        HookMatcher(matcher=None, hooks=[_post_tool_use]),
                        HookMatcher(matcher="CronCreate|CronDelete", hooks=[_on_cron_tool]),
                    ],
                    # PostToolUseFailure fires instead of PostToolUse when a tool
                    # errors out (or is interrupted) — without it, the entry would
                    # stay in _active_tools and the working-status line would keep
                    # showing the dead tool indefinitely.
                    "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[_post_tool_use])],
                },
                stderr=self._log_stderr,
                max_buffer_size=10 * 1024 * 1024,  # 10 MB — prevent crashes on large tool outputs
                extra_args=extra_args,
                include_partial_messages=True,
            )

            if resume:
                # Resume existing session
                options.resume = self.session_id
            else:
                # New session with custom session ID
                options.extra_args["session-id"] = self.session_id

            self._client = ClaudeSDKClient(options=options)
            patch_client_for_logging(self._client, self.session_id)

            # Connect without prompt to enter streaming mode, then send the message
            # via query(). The SDK's connect(prompt) with a string puts the transport
            # in "print mode" which closes stdin immediately, but ClaudeSDKClient
            # always uses streaming mode for the control protocol. We must connect()
            # first, then query() to send messages.
            await self._client.connect()

            # Build query prompt as async generator (streaming mode)
            query_prompt = self._build_query_prompt(prompt, images, documents)
            await self._client.query(query_prompt)

            logger.debug(
                "Connection established for session %s",
                self.session_id,
            )

            # Transition to assistant turn
            self._set_state(AgentState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()

            # Start background message loop
            self._message_loop_task = asyncio.create_task(
                self._run_message_loop(),
                name=f"claude_code-process-{self.session_id}",
            )

            logger.debug(
                "Message loop task created for session %s",
                self.session_id,
            )

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            await self._handle_error(f"Failed to start process: {e}", exc=e)

    async def set_permission_mode(self, mode: str) -> None:
        """Change permission mode on the live SDK client.

        Calls the SDK's set_permission_mode() method to update the permission mode
        on the running Claude process, then updates the local attribute to keep
        the in-memory state in sync.

        Args:
            mode: The permission mode to set (e.g., "default", "acceptEdits", "bypassPermissions")

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.debug(
            "Setting permission mode to '%s' for session %s",
            mode,
            self.session_id,
        )
        await self._client.set_permission_mode(mode)
        self.agent_settings = self.agent_settings._replace(permission_mode=mode)

    async def stop_subagent(self, subagent_id: str) -> None:
        """Stop a running subagent (Task) via the SDK.

        Calls the SDK's ``stop_task()`` method to gracefully stop a
        specific subagent running within this session's process. The
        CLI will emit a ``task_notification`` with ``status='stopped'``
        in the JSONL stream.

        Args:
            subagent_id: The subagent identifier (same as the SDK's task_id)

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.info(
            "Stopping subagent %s in session %s",
            subagent_id,
            self.session_id,
        )
        await self._client.stop_task(subagent_id)

    async def interrupt(self) -> None:
        """Send an interrupt signal to the CLI (equivalent to Ctrl+C).

        Sets the _interrupting flag so the message loop handles the resulting
        error response gracefully (clean DEAD state, no error toast).

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        self._interrupting = True
        logger.info("Interrupting session %s", self.session_id)
        await self._client.interrupt()

    async def interrupt_or_kill(self, reason: str) -> None:
        """Try to interrupt the process gracefully, fall back to kill.

        When the process is actively working (ASSISTANT_TURN, STARTING), sends
        an interrupt signal so the CLI can finalize JSONL writes. When idle
        (USER_TURN), kills directly since there's nothing to interrupt.

        Args:
            reason: Reason for stopping (e.g., "manual", "apply-settings")
        """
        if self.state in (AgentState.ASSISTANT_TURN, AgentState.STARTING):
            try:
                self.kill_reason = reason
                await self.interrupt()
                if await self.wait_for_dead():
                    return
                logger.debug(
                    "Interrupt timeout for session %s, falling back to kill",
                    self.session_id,
                )
            except Exception:
                logger.debug(
                    "Interrupt failed for session %s, falling back to kill",
                    self.session_id,
                )
        await self.kill(reason=reason)

    async def set_model(self, sdk_model: str | None) -> None:
        """Change the AI model on the live SDK client.

        Calls the SDK's set_model() method with the full SDK model string
        (including "[1m]" suffix for extended context if applicable).

        Note: this does NOT update ``self.agent_settings``. The caller
        (apply_live_settings) is responsible for that.

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.debug(
            "Setting SDK model to '%s' for session %s",
            sdk_model,
            self.session_id,
        )
        await self._client.set_model(sdk_model)

    def _build_sdk_model(self, selected_model: str | None = None, context_max: int | None = None) -> str | None:
        """Build the full SDK model string from model shorthand and context_max.

        Uses the model registry to resolve versioned models to their full SDK
        name, and appends "[1m]" only when the model supports extended context.
        """
        from twicc.providers.helpers import get_provider_helpers

        model = selected_model if selected_model is not None else self.agent_settings.selected_model
        ctx = context_max if context_max is not None else self.agent_settings.context_max
        return get_provider_helpers(Provider.CLAUDE_CODE).resolve_sdk_model(model, ctx)

    @property
    def sdk_model(self) -> str | None:
        """The current full SDK model string (including context suffix)."""
        return self._build_sdk_model()

    @staticmethod
    def _is_permission_mode_change_ack(msg: object) -> bool:
        """Check if a message is the CLI ack for a set_permission_mode control request.

        The CLI emits: SystemMessage(subtype="status", data={..., "permissionMode": "...", "status": None})
        """
        return (
            isinstance(msg, SystemMessage)
            and msg.subtype == "status"
            and "permissionMode" in msg.data
        )

    @staticmethod
    def _is_model_change_ack(msg: object) -> bool:
        """Check if a message is the CLI ack for a set_model control request.

        The CLI emits: UserMessage(content="<local-command-stdout>Set model to ...</local-command-stdout>")
        """
        return (
            isinstance(msg, UserMessage)
            and isinstance(msg.content, str)
            and msg.content.startswith("<local-command-stdout>Set model to ")
        )

    @staticmethod
    def _is_compacting_status(msg: object) -> bool:
        """Check if a message signals that the CLI is compacting the conversation context.

        The CLI emits: SystemMessage(subtype="status", data={..., "status": "compacting"})
        """
        return (
            isinstance(msg, SystemMessage)
            and msg.subtype == "status"
            and msg.data.get("status") == "compacting"
        )

    @staticmethod
    def _is_settings_change_ack(msg: object) -> bool:
        """Check if a message is an ack from a settings control request (not real assistant activity)."""
        return ClaudeCodeAgent._is_permission_mode_change_ack(msg) or ClaudeCodeAgent._is_model_change_ack(msg)

    async def apply_live_settings(self, settings: AgentSettings) -> None:
        """Apply live and idle setting changes to the live SDK client.

        Of the bundled :class:`AgentSettings`, only the fields that
        Claude Code classifies as live or idle are consulted here:
        ``permission_mode`` (live, applicable in any state), and
        ``selected_model`` + ``context_max`` (idle, applicable during
        USER_TURN). Other fields are ignored — they're either
        startup-only (handled by a restart elsewhere) or not consumed
        by the SDK on a live client.

        The SDK methods are called only when the requested value
        differs from the current one. Context_max is folded into the
        ``sdk_model`` string when set_model is called.
        """
        if settings.permission_mode != self.agent_settings.permission_mode:
            await self.set_permission_mode(settings.permission_mode)

        if self.state == AgentState.USER_TURN:
            new_sdk_model = self._build_sdk_model(settings.selected_model, settings.context_max)
            if new_sdk_model != self.sdk_model:
                await self.set_model(new_sdk_model)
                self.agent_settings = self.agent_settings._replace(
                    selected_model=settings.selected_model,
                    context_max=settings.context_max,
                )

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Send a follow-up message to the process.

        Args:
            text: The message text to send
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Raises:
            RuntimeError: If the process is not running or not ready for input
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        if self.state not in (AgentState.USER_TURN, AgentState.ASSISTANT_TURN):
            raise RuntimeError(f"Cannot send message in state {self.state}")

        logger.debug("Sending message to session %s", self.session_id)

        try:
            # Only transition and notify if we're not already in ASSISTANT_TURN
            # (if this case, Claude will queue the message, we stay in ASSISTANT_TURN)
            if self.state != AgentState.ASSISTANT_TURN:
                self._set_state(AgentState.ASSISTANT_TURN)
                self.last_activity = time.time()
                await self._notify_state_change()

            # Build query prompt as async generator (streaming mode)
            query_prompt = self._build_query_prompt(text, images, documents)
            await self._client.query(query_prompt)

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            await self._handle_error(f"Failed to send message: {e}", exc=e)

    async def kill(self, reason: str = "manual") -> None:
        """Terminate the process gracefully.

        This cancels the message loop and kills the underlying Claude CLI process.
        Safe to call multiple times or on an already dead process.

        Args:
            reason: Reason for killing the process (e.g., "manual", "shutdown")
        """
        logger.debug(
            "Kill requested for session %s (reason: %s)", self.session_id, reason
        )

        if self.state == AgentState.DEAD:
            logger.debug("Session %s already dead, skipping kill", self.session_id)
            return

        self.kill_reason = reason

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_all_pending_futures()

        # Cancel the message loop first so it doesn't interpret the imminent
        # transport close as a real error.
        if self._message_loop_task is not None:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        await self._shutdown_sdk_client()

        # Update state
        self._set_state(AgentState.DEAD)
        self.last_activity = time.time()
        self._first_turn_done_event.set()  # Unblock any waiters (cron restart)
        await self._notify_state_change()

    async def _run_message_loop(self) -> None:
        """Background task that consumes messages and tracks state.

        This loop processes messages from Claude. We don't use the message content
        (the JSONL watcher handles that), but we need to consume them to detect
        when Claude is done responding (ResultMessage).
        """
        if self._client is None:
            return

        logger.debug("Entering message loop for session %s", self.session_id)

        # State variables when streaming is about assistant messages and thinking
        current_stream_message_id: str | None = None
        current_stream_block_index: int | None = None
        current_stream_block_type: str | None = None
        # Fallback: if AssistantMessage arrives after content_block_stop,
        # the current_stream_block_* vars are already cleared. We keep the
        # last finished block info so we can still send the END event.
        last_finished_block_index: int | None = None
        last_finished_block_type: str | None = None

        # State variables when streaming is about tools
        current_stream_tool_id: str | None = None
        current_stream_tool_name: str | None = None
        current_stream_tool_input_raw: str = ""
        current_stream_tool_input_cleaned: dict | None = None

        try:
            async for msg in self._client.receive_messages():
                self.last_activity = time.time()

                if isinstance(msg, StreamEvent):
                    event = msg.event
                    event_type = event.get("type")
                    match event_type:
                        # HANDLE SIMPLE MESSAGES AND THINKING
                        case "message_start" if (
                            isinstance(message := event.get("message"), dict)
                            and message.get("type") == "message"
                            and message.get("role") == "assistant"
                            and (message_id := message.get("id"))
                        ):
                            # entering a new streaming message with id
                            current_stream_message_id = message_id
                        case "content_block_start" if (
                            current_stream_message_id
                            and (block_index := event.get("index")) is not None
                            and isinstance(block := event.get("content_block"), dict)
                            and (block_type := block.get("type")) in ("text", "thinking")
                        ):
                            current_stream_block_index = block_index
                            current_stream_block_type = block_type
                            last_finished_block_index = None
                            last_finished_block_type = None
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] => START")
                            await self._broadcast_stream_event({
                                "type": "stream_block_start",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                            })
                        case "content_block_delta" if (
                            current_stream_block_type
                            and isinstance(delta := event.get("delta"), dict)
                            and delta.get("type") == f"{current_stream_block_type}_delta"
                            and (delta_text := delta.get(current_stream_block_type))
                        ):
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] `{delta_text}`")
                            await self._broadcast_stream_event({
                                "type": "stream_block_delta",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                                "text": delta_text,
                            })
                        case "content_block_stop" if current_stream_block_type:
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] => STOP")
                            await self._broadcast_stream_event({
                                "type": "stream_block_stop",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                            })
                            last_finished_block_index = current_stream_block_index
                            last_finished_block_type = current_stream_block_type
                            current_stream_block_index = None
                            current_stream_block_type = None
                        case "message_stop" if current_stream_message_id:
                            current_stream_message_id = None
                            last_finished_block_index = None
                            last_finished_block_type = None

                        # HANDLE TOOL CALLS
                        case "content_block_start" if (
                            (block_index := event.get("index")) is not None
                            and isinstance(block := event.get("content_block"), dict)
                            and (block_type := block.get("type")) == "tool_use"
                            and (current_stream_tool_name := block.get("name"))
                            and (current_stream_tool_id := block.get("id"))
                        ):
                            current_stream_tool_input_raw = ""
                            current_stream_tool_input_cleaned = None
                            # Track the tool from its very first event so the delta
                            # handler's "skip if not tracked" guard can distinguish
                            # late zombie deltas from this tool's normal stream.
                            self._active_tools[current_stream_tool_id] = {
                                "name": current_stream_tool_name,
                                "input": {},
                                "streaming": True,
                            }
                            self._last_started_tool_id = current_stream_tool_id
                            # logger.debug(
                            #     "[ToolUse block_start index=%s tool=%s id=%s] => START",
                            #     block_index, current_stream_tool_name, current_stream_tool_id,
                            # )
                            await self._broadcast_process_tools()
                        case "content_block_delta" if (
                            current_stream_tool_id
                            and current_stream_tool_id in self._active_tools
                            and isinstance(delta := event.get("delta"), dict)
                            and delta.get("type") == "input_json_delta"
                            and (chunk := delta.get("partial_json"))
                        ):
                            current_stream_tool_input_raw += chunk
                            try:
                                parsed_input = json_repair.loads(current_stream_tool_input_raw)
                            except Exception as exc:
                                logger.exception(f"Failed to parse tool input: {exc}")
                            else:
                                if isinstance(parsed_input, dict):
                                    cleaned = filter_tool_input(current_stream_tool_name, parsed_input)
                                    if cleaned != current_stream_tool_input_cleaned:
                                        current_stream_tool_input_cleaned = cleaned
                                        self._active_tools[current_stream_tool_id]["input"] = cleaned
                                        # logger.debug(
                                        #     "[ToolUse content_block_delta tool=%s id=%s active_tools=%d] => input=%s",
                                        #     current_stream_tool_name, current_stream_tool_id,
                                        #     len(self._active_tools), cleaned,
                                        # )
                                        await self._broadcast_process_tools()

                        case "content_block_stop" if current_stream_tool_id:
                            # logger.debug(
                            #     "[ToolUse block_stop tool=%s id=%s] => STOP",
                            #     current_stream_tool_name, current_stream_tool_id,
                            # )
                            # Flip streaming=False as soon as the block ends — input is
                            # final, no more deltas. This is the earliest reliable point;
                            # PreToolUse-based flip stays as a redundant safety net.
                            if current_stream_tool_id in self._active_tools:
                                entry = self._active_tools[current_stream_tool_id]
                                if entry.get("streaming"):
                                    entry["streaming"] = False
                                    await self._broadcast_process_tools()
                            current_stream_tool_id = None
                            current_stream_tool_name = None
                            current_stream_tool_input_raw = ""
                            current_stream_tool_input_cleaned = None

                elif (
                    isinstance(msg, AssistantMessage)
                    and msg.message_id == current_stream_message_id
                    and (current_stream_block_type or last_finished_block_type)
                ):
                    # AssistantMessage usually arrives before content_block_stop (current_stream_block_*
                    # still set), but may arrive after (fallback to last_finished_block_*).
                    end_block_index = current_stream_block_index if current_stream_block_type else last_finished_block_index
                    end_block_type = current_stream_block_type or last_finished_block_type

                    # logger.debug(f"[Stream {end_block_type} msg_id={current_stream_message_id} index={end_block_index}] => END: uuid = {msg.uuid}")
                    await self._broadcast_stream_event({
                        "type": "stream_block_end",
                        "session_id": self.session_id,
                        "message_id": current_stream_message_id,
                        "block_index": end_block_index,
                        "block_type": end_block_type,
                        "uuid": msg.uuid,
                    })
                    # Clear last_finished so we don't re-match on a subsequent AssistantMessage
                    last_finished_block_index = None
                    last_finished_block_type = None

                elif isinstance(msg, AssistantMessage) and msg.error == "authentication_failed":
                    logger.error(
                        "Authentication failed for session %s — Claude Code CLI is not logged in",
                        self.session_id,
                    )
                    self._cancel_all_pending_futures()
                    self._set_state(AgentState.DEAD)
                    self.kill_reason = "auth_required"
                    self.last_activity = time.time()
                    self._first_turn_done_event.set()
                    await self._notify_state_change()
                    # Flip the global auth state immediately. The credentials file
                    # may still exist on disk, but the SDK has just told us the
                    # token is no longer accepted — that's the authoritative signal.
                    from twicc.providers.claude_code.auth import mark_unauthenticated_and_broadcast
                    await mark_unauthenticated_and_broadcast()
                    await self._shutdown_sdk_client()
                    return

                if isinstance(msg, ResultMessage):
                    # Claude finished responding, ready for user input
                    if msg.is_error:
                        if self._interrupting:
                            # Expected error after interrupt — clean shutdown.
                            # kill_reason is already set by interrupt_or_kill().
                            # No self.error = avoids the error toast on the frontend.
                            logger.info(
                                "Session %s interrupted cleanly (subtype=%s)",
                                self.session_id,
                                msg.subtype,
                            )
                            self._cancel_all_pending_futures()
                            self._set_state(AgentState.DEAD)
                            self.last_activity = time.time()
                            self._first_turn_done_event.set()
                            await self._notify_state_change()
                            await self._shutdown_sdk_client()
                            return

                        # Log full ResultMessage details for debugging
                        logger.error(
                            "Claude Code error for session %s: result=%r, subtype=%s, "
                            "num_turns=%s, duration_ms=%s",
                            self.session_id,
                            msg.result,
                            msg.subtype,
                            msg.num_turns,
                            msg.duration_ms,
                        )
                        await self._handle_error(
                            f"Claude reported error: {msg.result or 'Unknown error'}"
                        )
                        return

                    # Signal first USER_TURN for ProcessRun lifecycle
                    if not self._first_user_turn_reached:
                        self._first_user_turn_reached = True
                        self._first_turn_done_event.set()

                    # Turn ended — every tool of this turn is finished, so any
                    # remaining _active_tools entry is stale (typically resurrected
                    # by a late content_block_delta after its PostToolUse fired).
                    if self._active_tools:
                        self._active_tools.clear()
                        await self._broadcast_process_tools()

                    self._set_state(AgentState.USER_TURN)
                    await self._notify_state_change()

                elif self._is_compacting_status(msg):
                    # CLI is compacting context — notify frontend to show "compacting" label
                    await self._broadcast_process_label("compacting")

                elif self.state != AgentState.ASSISTANT_TURN:
                    # Enforce assistant state if another message came after the ResultMessage.
                    # Skip ack messages from control requests (set_permission_mode, set_model)
                    # as they don't represent real assistant activity.
                    if not self._is_settings_change_ack(msg):
                        self._set_state(AgentState.ASSISTANT_TURN)
                        await self._notify_state_change()

        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            raise
        except (ClaudeSDKError, Exception) as e:
            # If the process was already killed intentionally (apply-settings, manual, shutdown),
            # the message loop error is expected (CLI exiting) — don't treat it as an error.
            if self.state == AgentState.DEAD and self.kill_reason and self.kill_reason != "error":
                logger.debug(
                    "Message loop ended after intentional kill for session %s (kill_reason=%s): %s",
                    self.session_id, self.kill_reason, e,
                )
                return
            prefix = "SDK error" if isinstance(e, ClaudeSDKError) else "Unexpected error in message loop"
            await self._handle_error(f"{prefix}: {e}", exc=e)

    async def _handle_error(self, error_message: str, exc: Exception | None = None) -> None:
        """Handle an error by transitioning to DEAD state.

        Args:
            error_message: Description of what went wrong
            exc: Optional exception that caused the error (logged with full traceback)
        """
        logger.error(
            "Process %s for session %s died: %s",
            self.project_id,
            self.session_id,
            error_message,
            exc_info=exc,
        )

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_all_pending_futures()

        self._set_state(AgentState.DEAD)
        self.error = error_message
        self.kill_reason = "error"
        self.last_activity = time.time()
        self._first_turn_done_event.set()  # Unblock any waiters (cron restart)

        await self._notify_state_change()

        await self._shutdown_sdk_client()

    async def _shutdown_sdk_client(self) -> None:
        """Release the SDK client and its CLI subprocess.

        Calls ``ClaudeSDKClient.disconnect()`` — the SDK's intended cleanup
        path — so it can cancel its detached read / control-handler tasks,
        close the message stream, and shut the transport (which kills the
        Claude CLI subprocess). The call is bounded by a 5 s timeout and
        shielded against caller cancellation so a hanging or misbehaving
        disconnect can't propagate cancellation up to our task.

        Earlier SDK versions held those detached tasks inside an anyio
        ``TaskGroup`` whose ``CancelScope`` had task affinity — closing the
        transport from a task other than the one that opened it raised
        ``RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in``. The SDK now spawns them via plain
        ``loop.create_task`` (see ``claude_agent_sdk/_internal/_task_compat``),
        so ``disconnect()`` is safe to call from any task in the version
        range we pin.

        If disconnect fails or times out, falls back to killing the
        subprocess directly via psutil so we never leave the CLI running.
        We deliberately skip the psutil kill on the success path to avoid a
        narrow PID-reuse window: between transport close and our kill, the
        OS may have already recycled the pid for an unrelated process.

        Safe to call when ``_client`` is already ``None`` — no-op.
        """
        client = self._client
        if client is None:
            return
        pid = self.get_pid()
        self._client = None
        try:
            await asyncio.wait_for(
                asyncio.shield(client.disconnect()),
                timeout=5.0,
            )
            return
        except asyncio.TimeoutError:
            logger.warning(
                "SDK disconnect() timed out for session %s — falling back to psutil kill",
                self.session_id,
            )
        except Exception:
            logger.exception(
                "SDK disconnect() failed for session %s — falling back to psutil kill",
                self.session_id,
            )
        if pid is not None:
            await self._kill_system_process(pid)

    async def _kill_system_process(self, pid: int) -> None:
        """Kill the system process and all its children, isolated from anyio.

        Uses psutil to find and kill all child processes recursively (including
        bash commands spawned by tools). Falls back to single process kill if
        psutil fails.

        Uses SIGTERM first for graceful shutdown, then SIGKILL after timeout.
        Runs in a thread executor to avoid any async context pollution that
        could cause cancel scope leaks.

        Args:
            pid: Process ID to kill
        """
        import psutil

        def _do_kill() -> None:
            """Synchronous kill logic, runs in a separate thread."""
            try:
                parent = psutil.Process(pid)
            except psutil.NoSuchProcess:
                logger.debug("Process %d already dead", pid)
                return

            # Get all children recursively BEFORE killing parent
            # (once parent is dead, children become orphans and harder to find)
            try:
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []

            all_procs = children + [parent]  # Kill children first, then parent
            logger.debug("Killing process %d and %d children", pid, len(children))

            # SIGTERM to all processes
            for proc in all_procs:
                try:
                    proc.terminate()  # SIGTERM
                    logger.debug("Sent SIGTERM to process %d", proc.pid)
                except psutil.NoSuchProcess:
                    pass

            # Wait for graceful termination (up to 2 seconds)
            gone, alive = psutil.wait_procs(all_procs, timeout=2)

            if gone:
                logger.debug("%d process(es) terminated gracefully", len(gone))

            # SIGKILL any survivors
            for proc in alive:
                try:
                    logger.warning(
                        "Process %d did not terminate after SIGTERM, sending SIGKILL",
                        proc.pid,
                    )
                    proc.kill()  # SIGKILL
                except psutil.NoSuchProcess:
                    pass

        # Run in thread executor for complete isolation from async context
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_kill)

    async def _update_plan(self, new_plan: str) -> None:
        """Handle a user-modified plan for ExitPlanMode.

        Called when the user approves ExitPlanMode with changes and the plan
        content differs from the original. Retrieves the session slug from the
        database, then overwrites the plan file at ~/.claude/plans/{slug}.md.

        Args:
            new_plan: The modified plan content from the user
        """
        from pathlib import Path

        slug = await self._get_session_slug(self.session_id)
        if slug is None:
            logger.warning(
                "Cannot update plan for session %s: no slug found in session items",
                self.session_id,
            )
            return

        plan_file = Path.home() / ".claude" / "plans" / f"{slug}.md"
        if not plan_file.exists():
            logger.warning(
                "Plan file does not exist for session %s: %s",
                self.session_id,
                plan_file,
            )
            return

        try:
            plan_file.write_text(new_plan, encoding="utf-8")
            logger.info(
                "Plan file updated for session %s: %s (%d chars)",
                self.session_id,
                plan_file,
                len(new_plan),
            )
        except OSError as e:
            logger.error(
                "Failed to write plan file for session %s: %s",
                self.session_id,
                e,
            )

    async def _broadcast_stream_event(self, data: dict) -> None:
        """Broadcast a streaming event to all connected WebSocket clients."""
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": data},
        )

    async def _broadcast_process_label(self, label: str) -> None:
        """Broadcast a transient label override for the process status display."""
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "process_label",
                "session_id": self.session_id,
                "label": label,
            }},
        )

    async def _broadcast_process_tools(self) -> None:
        """Broadcast the current list of in-progress tools for the status display."""
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "process_tools",
                "session_id": self.session_id,
                "tools": self._serialize_active_tools(),
                "last_started_id": self._last_started_tool_id,
            }},
        )
