"""
Session title management.

Provides title validation, a pending-title store for draft sessions
(where the JSONL file doesn't exist yet), and a thin wrapper around
the Claude Agent SDK's rename_session() for writing custom-title
entries to JSONL files.
"""

import logging
from typing import NamedTuple

from claude_agent_sdk import rename_session

logger = logging.getLogger(__name__)

# Global dict for pending titles (draft sessions only)
_pending_titles: dict[str, str] = {}


class TitleCheck(NamedTuple):
    """Result of checking a title against protection."""
    should_apply: bool
    correction: str | None = None


# Titles protected from CLI stale re-appends.
# Set when the user renames via the API; cleared when the CLI absorbs the
# correct value or when the process dies.
_protected_titles: dict[str, str] = {}

# Max title length (matches frontend validation)
MAX_TITLE_LENGTH = 200


def validate_title(title: str | None) -> tuple[str | None, str | None]:
    """Validate and normalize a session title.

    Returns:
        A tuple of (normalized_title, error_message).
        - If valid: (trimmed_title, None)
        - If invalid: (None, error_message)
    """
    if title is None:
        return None, "Title cannot be empty"

    title = title.strip()
    if not title:
        return None, "Title cannot be empty"

    if len(title) > MAX_TITLE_LENGTH:
        return None, f"Title must be {MAX_TITLE_LENGTH} characters or less"

    return title, None


def rename_session_in_jsonl(session_id: str, title: str) -> None:
    """Write a custom-title entry to the session's JSONL file via the SDK.

    Uses the SDK's rename_session() which performs a kernel-level atomic
    append (O_APPEND + os.write). Safe to call at any time, even while
    a Claude CLI process is actively writing to the file.

    Silently logs and returns on failure (file not found, etc.) —
    the DB title is already updated by the caller, and the watcher
    will eventually sync if the entry is missing.
    """
    try:
        rename_session(session_id, title)
    except Exception as e:
        logger.warning("rename_session failed for %s: %s", session_id, e)
        raise


def set_pending_title(session_id: str, title: str) -> None:
    """Store a title for a draft session (JSONL doesn't exist yet)."""
    _pending_titles[session_id] = title
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Get a pending title for a session without removing it."""
    return _pending_titles.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove a pending title for a session."""
    return _pending_titles.pop(session_id, None)


def protect_title(session_id: str, title: str) -> None:
    """Mark a title as protected from CLI stale re-appends."""
    _protected_titles[session_id] = title
    logger.debug("Protected title for session %s: %s", session_id, title[:50])


def get_protected_title(session_id: str) -> str | None:
    """Get the protected title for a session without removing it."""
    return _protected_titles.get(session_id)


def check_protected_title(session_id: str, incoming_title: str) -> TitleCheck:
    """Check an incoming custom-title from JSONL against protection.

    Returns:
        TitleCheck with should_apply=True if the title should be applied to DB,
        or should_apply=False with correction set to the correct title to re-write.
    """
    protected = get_protected_title(session_id)
    if protected is None:
        return TitleCheck(should_apply=True)

    if incoming_title == protected:
        # Correct title — apply it. Don't clear protection: we can't distinguish
        # our own SDK write from the CLI absorbing the value. Protection is only
        # cleared on process DEAD (via clear_protected_title).
        return TitleCheck(should_apply=True)

    # Stale title from CLI — block and return correction
    logger.info(
        "Blocked stale title for session %s: %r -> keeping %r",
        session_id, incoming_title, protected,
    )
    return TitleCheck(should_apply=False, correction=protected)


def clear_protected_title(session_id: str) -> None:
    """Remove title protection for a session (e.g. when the process dies)."""
    if _protected_titles.pop(session_id, None):
        logger.debug("Cleared title protection for session %s (process died)", session_id)
