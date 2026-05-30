"""Local DB lookup of an existing session.

Shared by ``twicc send-message`` and ``twicc update-session``. The CLI side
does this pre-check before dropping the request so the user gets an immediate,
locally diagnosable error for the common cases (session id unknown, session
stale because its JSONL was deleted, project without directory, subagent).
The watcher-side service re-validates the same conditions in case the DB
state changed between this lookup and the actual operation.
"""

from __future__ import annotations

from typing import NamedTuple


class SessionLookupError(Exception):
    """Raised when the local pre-check for an existing session fails.

    ``code`` is the same vocabulary the backend services use
    (``session_not_found``, ``session_stale``, ``project_no_directory``,
    ``unknown_provider``, ``is_subagent``) so the output layer can render a
    uniform message regardless of where the failure was detected.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ResolvedSession(NamedTuple):
    session_id: str
    provider: str  # value (e.g. "claude_code"); validated against the Provider enum
    project_id: str
    directory: str
    hidden: bool


def lookup_session(session_id: str) -> ResolvedSession:
    """Read the session row and validate it can be the target of a send or update.

    Raises :class:`SessionLookupError` on any failed precondition; otherwise
    returns a :class:`ResolvedSession` with the fields the caller needs to
    keep going (notably ``provider`` to pick the right attachment caps).
    """
    from twicc.core.enums import Provider
    from twicc.core.models import Session, SessionType

    session = (
        Session.objects.select_related("project").filter(id=session_id).first()
    )
    if session is None:
        raise SessionLookupError(
            "session_not_found",
            f"Session {session_id!r} not found",
        )
    if session.type == SessionType.SUBAGENT:
        raise SessionLookupError(
            "is_subagent",
            f"Session {session_id!r} is a subagent; subagents cannot be "
            "messaged or updated directly. Target the parent session instead.",
        )
    if session.stale:
        raise SessionLookupError(
            "session_stale",
            f"Session {session_id!r} is stale "
            "(its JSONL file no longer exists on disk)",
        )
    project = session.project
    if project is None or not project.directory:
        raise SessionLookupError(
            "project_no_directory",
            f"Session {session_id!r} has no project directory configured",
        )
    try:
        Provider(session.provider)
    except ValueError:
        raise SessionLookupError(
            "unknown_provider",
            f"Session {session_id!r} has unknown provider {session.provider!r}",
        )
    return ResolvedSession(
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        directory=project.directory,
        hidden=bool(session.hidden),
    )
