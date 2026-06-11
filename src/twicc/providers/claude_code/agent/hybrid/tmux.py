"""Tmux primitives for hybrid CLI sessions.

A hybrid session owns exactly one tmux session named ``twicc-hybrid-<id>``
(sanitized), on the shared ``-L twicc`` socket, whose single pane runs the
Claude CLI directly (via ``exec``) so the pane PID *is* the claude PID and
``pane_dead`` flips when claude exits (``remain-on-exit on``).

All functions are sync — callers wrap them with ``asyncio.to_thread``.
"""

import os
import shlex
import subprocess

from twicc.terminal import TMUX_SOCKET_NAME, get_tmux_path, resolve_tmux_config_path

HYBRID_SESSION_PREFIX = "twicc-hybrid-"

# Prefix-based purge, same intent as ClaudeCodeHelpers.purge_env_vars:
# CLAUDE_CODE* and CLAUDECODE*. ``env -u`` needs exact names, so expand the
# prefixes against the CURRENT environment at build time, and always add the
# known marker names below: the tmux server keeps the environment of whoever
# first started it, so a marker can reach the pane through the server even
# when the backend's own environment is clean.
_PURGED_ENV_PREFIXES = ("CLAUDE_CODE", "CLAUDECODE")

# Markers the CLI injects into the environment of its subprocesses (Bash
# tools). Leftovers are NOT harmless: CLAUDE_CODE_CHILD_SESSION alone makes
# a CLI >= 2.1.171 treat itself as a child session and silently skip
# transcript persistence entirely — nothing is written (not even at a
# graceful exit) and --resume answers "No conversation found" (regression of
# the upstream 2.1.170 inherited-env fix; bisected on 2.1.172, 2026-06-11).
_PURGED_ENV_NAMES = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_EXECPATH",
)


def _purged_env_names() -> list[str]:
    names = {name for name in os.environ if name.startswith(_PURGED_ENV_PREFIXES)}
    names.update(_PURGED_ENV_NAMES)
    return sorted(names)


def hybrid_tmux_session_name(session_id: str) -> str:
    """Tmux session name for a hybrid session (disjoint from the Terminal
    panel's ``twicc-<id>`` namespace)."""
    return HYBRID_SESSION_PREFIX + session_id.replace(".", "_").replace(":", "_")


def _tmux_base() -> list[str]:
    tmux = get_tmux_path()
    if tmux is None:
        raise RuntimeError("tmux is not installed — hybrid mode requires tmux")
    return [tmux, "-L", TMUX_SOCKET_NAME]


def _run(args: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_tmux_base(), *args],
        input=input_bytes,
        capture_output=True,
        timeout=5,
        check=False,
    )


def _read_tmux_config_path() -> str | None:
    """User-configured tmux config, resolved like the Terminal panel does."""
    from twicc.synced_settings import read_synced_settings

    configured = read_synced_settings().get("terminalTmuxConfigPath") or ""
    return resolve_tmux_config_path(configured)


def session_exists(session_id: str) -> bool:
    # '=' forces exact-name match (no prefix matching).
    return _run(["has-session", "-t", "=" + hybrid_tmux_session_name(session_id)]).returncode == 0


def create_session(session_id: str, cwd: str, argv: list[str]) -> None:
    """Create the tmux session running ``argv`` directly as the pane command.

    ``exec env -u VAR…`` ensures the ``sh -c`` wrapper is replaced and the
    purged variables never reach claude, regardless of the tmux server env.
    """
    name = hybrid_tmux_session_name(session_id)
    unsets: list[str] = []
    for var in _purged_env_names():
        unsets += ["-u", var]
    command = "exec env " + shlex.join(unsets + argv) if unsets else "exec " + shlex.join(argv)
    base = _tmux_base()
    config = _read_tmux_config_path()
    base += ["-f", config if config else "/dev/null"]
    result = subprocess.run(
        [*base, "new-session", "-d", "-s", name, "-c", cwd, command],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux new-session failed: {result.stderr.decode(errors='replace')}")
    # NOTE: set-option's -t is a target-PANE (options exist at every level
    # since tmux 3.x): a bare '=name' fails with "no such session" — and
    # _run ignores return codes here, so the failure would be silent
    # (it was, before the '=name:' form fixed it). Same form as paste/capture.
    target = "=" + name + ":"
    _run(["set-option", "-t", target, "remain-on-exit", "on"])
    _run(["set-option", "-t", target, "mouse", "off"])
    # The embedded composer terminal shows ONLY the claude TUI — the tmux
    # status bar would waste a line and wrap noisily at narrow widths.
    _run(["set-option", "-t", target, "status", "off"])


def pane_status(session_id: str) -> tuple[int | None, bool]:
    """Return ``(pane_pid, pane_dead)``; ``(None, True)`` if the session is gone."""
    result = _run(
        ["list-panes", "-t", "=" + hybrid_tmux_session_name(session_id), "-F", "#{pane_pid} #{pane_dead}"]
    )
    if result.returncode != 0:
        return None, True
    parts = result.stdout.decode().split()
    if len(parts) < 2:
        return None, True
    return int(parts[0]), parts[1] == "1"


def _pane_target(session_id: str) -> str:
    """Exact-match target-PANE for the session's active pane.

    Pane-target commands (``paste-buffer``, ``send-keys``, ``capture-pane``)
    reject a bare ``=name`` ("can't find pane") — the exact-session form for
    a pane is ``=name:`` (trailing colon = active window/pane). Verified
    empirically; session-target commands keep the bare ``=name`` form.
    """
    return "=" + hybrid_tmux_session_name(session_id) + ":"


def capture_pane(session_id: str) -> str | None:
    """Return the visible pane content, or ``None`` if the session is gone.

    Used to detect TUI dialogs that must clear before a paste (the trust
    dialog swallows pasted text entirely — verified empirically).
    """
    result = _run(["capture-pane", "-p", "-t", _pane_target(session_id)])
    if result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


def paste_text(session_id: str, text: str, *, submit: bool = True) -> None:
    """Bracketed-paste ``text`` into the TUI composer, then press Enter.

    Verified: multiline pastes don't auto-submit, ``@`` mentions don't open
    the picker, pasted slash commands are interpreted on submit.
    """
    target = _pane_target(session_id)
    buf = "twicc-hybrid"
    r = _run(["load-buffer", "-b", buf, "-"], input_bytes=text.encode())
    if r.returncode != 0:
        raise RuntimeError(f"tmux load-buffer failed: {r.stderr.decode(errors='replace')}")
    r = _run(["paste-buffer", "-p", "-d", "-b", buf, "-t", target])
    if r.returncode != 0:
        raise RuntimeError(f"tmux paste-buffer failed: {r.stderr.decode(errors='replace')}")
    if submit:
        _run(["send-keys", "-t", target, "Enter"])


def kill_session(session_id: str) -> None:
    _run(["kill-session", "-t", "=" + hybrid_tmux_session_name(session_id)])


def list_hybrid_sessions() -> list[str]:
    """Return raw tmux session names with the hybrid prefix (boot adoption)."""
    result = _run(["list-sessions", "-F", "#{session_name}"])
    if result.returncode != 0:
        return []
    return [n for n in result.stdout.decode().splitlines() if n.startswith(HYBRID_SESSION_PREFIX)]
