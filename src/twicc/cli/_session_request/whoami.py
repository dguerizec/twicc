"""PID-ancestry lookup against ``ProcessRun.agent_pid``.

Powers ``twicc whoami`` and the silent auto-fill of ``spawned_by`` in
``twicc create-session``. The strategy is intentionally cheap:

1. One DB read returns every non-DEAD ``ProcessRun`` with its
   ``agent_pid`` and ``session_id``.
2. We then walk the local PID chain (``os.getpid() → ppid → … → 1``)
   and stop on the first match — that's the closest live agent.

The closest-match semantics matter for nested cases: if a session A
spawns session B, and a Bash tool inside B calls ``twicc``, the chain
is ``twicc → bash → claude(B) → backend Python → …``. ``agent_pid``
of B is closer than A in the chain, so we resolve to B. Each level
of nesting works the same way.

Returns the resolved ``Session`` (full row, so callers can serialise
the same shape as ``twicc session <ID>``) or ``None`` when no match
is found in the ancestry (e.g. a human running ``twicc`` from a
plain terminal).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator

logger = logging.getLogger(__name__)


def _walk_ppids() -> Iterator[int]:
    """Yield successive parent PIDs starting at ``os.getpid()`` up to PID 1."""
    pid = os.getpid()
    while pid is not None and pid > 1:
        ppid = _get_ppid(pid)
        if ppid is None or ppid <= 0:
            return
        yield ppid
        pid = ppid


def _get_ppid(pid: int) -> int | None:
    """Return the parent PID of ``pid``, or ``None`` if unobtainable.

    Prefer ``psutil`` when available; fall back to ``/proc/<pid>/status``
    on Linux. ``ps -o ppid=`` is the last resort for macOS / BSD without
    psutil.
    """
    try:
        import psutil  # type: ignore[import-untyped]
        return psutil.Process(pid).ppid()
    except ImportError:
        pass
    except Exception:
        # Process gone / permission error for this pid: fall through to
        # /proc and ps for the same pid rather than aborting the walk.
        pass

    # Linux fallback
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # macOS fallback via `ps`
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        pass

    return None


def resolve_current_session():
    """Return the ``Session`` of the closest live agent in the PID ancestry.

    Returns ``None`` when no ``ProcessRun.agent_pid`` matches any
    ancestor — typically the case for a human invoking ``twicc`` from
    a plain shell.

    The caller must have run ``django.setup()`` before this — the
    function does not bootstrap Django itself, to keep cold-start
    paths optional.
    """
    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun, Session

    # One DB read returns every live agent_pid → session_id pair.
    pid_to_session_id = dict(
        ProcessRun.objects.exclude(state=AgentState.DEAD)
        .exclude(agent_pid__isnull=True)
        .values_list("agent_pid", "session_id")
    )
    if not pid_to_session_id:
        return None

    for ppid in _walk_ppids():
        sid = pid_to_session_id.get(ppid)
        if sid is not None:
            try:
                return Session.objects.get(pk=sid)
            except Session.DoesNotExist:
                # ProcessRun outlived the Session row (e.g. session deleted
                # while process still alive). Fall through; nothing else to
                # match in the ancestry.
                return None

    logger.debug(
        "resolve_current_session: no matching agent in PID ancestry "
        "from %d (checked %d candidate PIDs)",
        os.getpid(), len(pid_to_session_id),
    )
    return None
