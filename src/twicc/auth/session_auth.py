"""Session ↔ password-hash binding.

A Django session marked ``authenticated=True`` is otherwise valid until
its cookie expires (~30 days), regardless of what happens to
``TWICC_PASSWORD_HASH`` in the meantime. That means rotating the
password — typically because it leaked — would not actually log out a
sitting attacker.

To prevent that, every authenticated session also stores a short
fingerprint of the password hash that was current at login time. Every
auth check compares that fingerprint to the fingerprint of the
*currently* configured hash; a mismatch invalidates the session.

The fingerprint is a one-way digest of the stored hash. It does not
reveal the password (or the hash itself), but it changes whenever the
password is set, cleared, or upgraded from legacy SHA-256 to PBKDF2.
"""

import hashlib

SESSION_AUTH_KEY = "authenticated"
SESSION_FINGERPRINT_KEY = "auth_fingerprint"

# 16 hex chars = 64 bits. Far more than enough to detect a hash change
# (we only ever compare equality), and short enough to keep the cookie
# payload tidy.
_FINGERPRINT_LENGTH = 16


def compute_fingerprint(stored_hash: str) -> str:
    """Return a short, deterministic, one-way fingerprint of the stored hash."""
    if not stored_hash:
        return ""
    return hashlib.sha256(stored_hash.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]


def bind_session(session, stored_hash: str) -> None:
    """Mark a Django session as authenticated against ``stored_hash``."""
    session[SESSION_AUTH_KEY] = True
    session[SESSION_FINGERPRINT_KEY] = compute_fingerprint(stored_hash)


def is_session_authenticated(
    session_auth: bool | None,
    session_fingerprint: str | None,
    stored_hash: str,
) -> bool:
    """Return True if the session is logged in AND bound to the current hash.

    Pre-extracted values are passed in (rather than the session itself)
    so the same helper works from sync HTTP code and from async WS
    consumers — the caller decides how to read the session keys.
    """
    if not session_auth or not stored_hash:
        return False
    return session_fingerprint == compute_fingerprint(stored_hash)
