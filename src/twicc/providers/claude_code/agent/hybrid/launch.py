"""CLI launch builder for hybrid sessions.

Builds the exact ``claude`` argv equivalent of what the SDK agent configures
through ``ClaudeAgentOptions`` (see ``agent.py``'s options builder — every
decision here mirrors it one-for-one), plus the hybrid-specific pieces:
the single injected ``PermissionRequest`` hook, forced file checkpointing,
the temp title and the attachments directory.

All flags verified empirically on the bundled CLI 2.1.170 (design doc §3.2).
"""

import shlex
from pathlib import Path

import orjson

from twicc.agent.plugin import get_plugin_dir
from twicc.core.enums import Provider
from twicc.paths import get_hybrid_hooks_dir, get_session_hybrid_dir
from twicc.providers.claude_code.bin import resolve_bundled_binary
from twicc.providers.helpers import AgentSettings, get_provider_helpers


def build_hooks_settings(session_id: str, fast_mode: bool) -> str:
    """Inline ``--settings`` JSON: fastMode + forced file checkpointing + the
    single ``PermissionRequest`` hook.

    Schema validated empirically on CLI 2.1.170 (2026-06-11 design probe).
    The hook is pure shell — it drops its stdin JSON into the watched
    hybrid-hooks directory (no HTTP endpoint, no token; filesystem write
    access is the authentication). ``$$`` + nanoseconds make the name
    unique; the ``.tmp`` → ``mv`` rename makes the drop atomic (the watcher
    ignores ``.tmp`` files).
    """
    hooks_dir = shlex.quote(str(get_hybrid_hooks_dir()))
    name = f"{session_id}__PermissionRequest__$$-$(date +%s%N)"
    command = f'f={hooks_dir}/{name}.json; cat > "$f.tmp" && mv "$f.tmp" "$f" || true'
    settings = {
        "fastMode": fast_mode,
        # Forced ON: off by default in SDK mode and user-disablable in user
        # settings; the original-file capture (compute) depends on it. The
        # env purge already drops an inherited
        # CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING.
        "fileCheckpointingEnabled": True,
        "hooks": {
            "PermissionRequest": [{"hooks": [{"type": "command", "command": command}]}],
        },
    }
    return orjson.dumps(settings).decode()


def build_argv(
    *,
    session_id: str,
    settings: AgentSettings,
    resume: bool,
    temp_title: str,
    addendum_path: Path | None,
    add_dirs: list[str],
    untrusted: bool,
) -> list[str]:
    """Build the full claude argv for a hybrid launch.

    ``settings`` must be the RESOLVED bundle (no ``None`` placeholders left
    for fields the provider uses). ``untrusted`` is the project's effective
    trust, resolved by the caller the same way the SDK agent does
    (``project_is_untrusted``); the permission-mode security floor is
    re-applied here regardless of what the stored session value says.
    ``add_dirs`` carries the session work dirs (artifacts/scratch, mirroring
    the SDK's ``add_dirs``) plus the hybrid attachments dir.
    """
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    argv = [str(resolve_bundled_binary())]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    sdk_model = helpers.resolve_sdk_model(settings.selected_model, settings.context_max)
    if sdk_model:
        argv += ["--model", sdk_model]
    if settings.effort:
        argv += ["--effort", settings.effort]
    # Mirror the SDK thinking mapping: True → adaptive, False → disabled,
    # None → omit (CLI default).
    if settings.thinking_enabled is True:
        argv += ["--thinking", "adaptive"]
    elif settings.thinking_enabled is False:
        argv += ["--thinking", "disabled"]
    permission_mode = settings.permission_mode
    if untrusted:
        # Security floor (trust design §13.4): clamp + user-only settings
        # sources (project/local settings, hooks and .mcp.json are
        # repo-controlled and must not shape the session).
        from twicc.core.services.trust import clamp_permission_mode_for_untrusted

        permission_mode = clamp_permission_mode_for_untrusted(Provider.CLAUDE_CODE, permission_mode)
        argv += ["--setting-sources", "user"]
    else:
        # Opt-in for bypassPermissions, withheld in untrusted projects.
        argv += ["--allow-dangerously-skip-permissions"]
        argv += ["--setting-sources", "user,project,local"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    # Mirror the SDK chrome mapping (truthy → --chrome, else --no-chrome).
    argv += ["--chrome"] if settings.claude_in_chrome else ["--no-chrome"]
    if settings.question_widget is False:
        argv += ["--disallowedTools", "AskUserQuestion"]
    argv += ["--settings", build_hooks_settings(session_id, bool(settings.fast_mode))]
    argv += ["--plugin-dir", str(get_plugin_dir())]
    for add_dir in add_dirs:
        argv += ["--add-dir", add_dir]
    if addendum_path is not None:
        argv += ["--append-system-prompt-file", str(addendum_path)]
    if temp_title:
        argv += ["-n", temp_title[:100]]
    return argv


def write_addendum_file(session_id: str, addendum: str | None) -> Path | None:
    """Persist the frozen system-prompt addendum for ``--append-system-prompt-file``.

    A file (hidden flag, verified) rather than an inline argument: the
    addendum is multi-KB and shell-quoting it into the pane command would be
    fragile. Rewritten at every launch — content is byte-stable by design
    (see ``Session.system_prompt_addendum``).
    """
    if not addendum:
        return None
    path = get_session_hybrid_dir(session_id) / "addendum.md"
    path.write_text(addendum, encoding="utf-8")
    return path
