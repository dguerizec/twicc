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
from collections.abc import Iterator

import psutil

logger = logging.getLogger(__name__)


def _walk_ppids() -> Iterator[int]:
    """Yield successive parent PIDs starting at ``os.getpid()`` up to PID 1."""
    pid = os.getpid()
    while pid > 1:
        try:
            ppid = psutil.Process(pid).ppid()
        except psutil.Error:
            # Process gone / permission error: stop the walk.
            return
        if ppid is None or ppid <= 0:
            return
        yield ppid
        pid = ppid


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


def resolve_spawned_by_filter(value: str | None) -> str | None:
    """Translate a ``--spawned-by`` CLI value into a session_id filter.

    - ``None``  → ``None`` (no filter)
    - ``"self"`` → resolve via whoami; raise ``RuntimeError`` on any failure
      (missing session in ancestry, DB error, ...)
    - any other string → use it verbatim as a session_id

    Any internal failure is wrapped in ``RuntimeError`` so the typer
    wrappers that call this can surface a clean human error + non-zero
    exit code instead of leaking a raw traceback.
    """
    if value is None:
        return None
    if value == "self":
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                f"--spawned-by self: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                "--spawned-by self: no TwiCC session found in PID ancestry. "
                "This flag is only meaningful from inside an active session.",
            )
        return session.id
    return value


def resolve_spawn_root_filter(value: str | None) -> str | None:
    """Translate a ``--spawn-root`` CLI value into a session_id filter.

    - ``None``  → ``None`` (no filter)
    - ``"self"`` → resolve via whoami; returns the current session's
      ``spawn_root_id`` if set, else its own id (the "I am my own root"
      fallback — matches the invariant established by ``twicc topology``).
      Raises ``RuntimeError`` on any failure.
    - any other string → use it verbatim as a session_id

    Any internal failure is wrapped in ``RuntimeError`` so the typer
    wrappers that call this can surface a clean human error + non-zero
    exit code instead of leaking a raw traceback.
    """
    if value is None:
        return None
    if value == "self":
        try:
            session = resolve_current_session()
        except Exception as e:
            raise RuntimeError(
                f"--spawn-root self: could not resolve the current "
                f"session: {type(e).__name__}: {e}",
            ) from e
        if session is None:
            raise RuntimeError(
                "--spawn-root self: no TwiCC session found in PID ancestry. "
                "This flag is only meaningful from inside an active session.",
            )
        return session.spawn_root_id or session.id
    return value
