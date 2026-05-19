"""Password hashing and verification for TwiCC.

Stored format
-------------
The string written to ``TWICC_PASSWORD_HASH`` is self-describing so the
verifier can dispatch on it without any side configuration:

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

A 64-character hex string with no ``$`` is treated as a legacy SHA-256
digest (the original format, used until salted PBKDF2 was added).

Legacy hashes stay valid; users transparently upgrade to PBKDF2 the next
time they run ``twicc password set``. That keeps existing setups working
without forcing a manual reset.

This module is deliberately stdlib-only so it can be imported from the
CLI (no Django setup) and from the Django views alike.
"""

import base64
import binascii
import hashlib
import hmac
import secrets

# OWASP 2023 recommendation for PBKDF2-SHA256. Tunable upward later — the
# iteration count is encoded in the stored hash, so raising the default
# does not break verification of existing hashes.
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_BYTES = 16
_PBKDF2_PREFIX = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and return the encoded string."""
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash (PBKDF2 or legacy SHA-256)."""
    if not stored:
        return False

    if stored.startswith(_PBKDF2_PREFIX + "$"):
        return _verify_pbkdf2(password, stored)

    # Legacy: bare SHA-256 hex digest, no salt, no iterations.
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored)


def is_legacy_hash(stored: str) -> bool:
    """Return True if the stored hash uses the legacy (unsalted SHA-256) format."""
    return bool(stored) and not stored.startswith(_PBKDF2_PREFIX + "$")


def _verify_pbkdf2(password: str, stored: str) -> bool:
    try:
        _, iterations_s, salt_b64, hash_b64 = stored.split("$", 3)
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, binascii.Error):
        return False
    if iterations <= 0:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)
