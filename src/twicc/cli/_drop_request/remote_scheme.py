"""The ``remote:`` path scheme — resolve a path on the remote server, not locally.

Over ``--remote`` a bare file path is read on the *client* (the forwarder inlines
prompt/message files as text and ``--attach`` files as ``data:`` URIs). Prefixing
the path with ``remote:`` flips that: the forwarder strips the scheme and sends
the bare path, leaving the *server* to read it from its own filesystem.

Modeled on ``data:`` — ``remote:`` directly followed by the path, with no slashes
of its own. The path must be **absolute** (the server has no caller working
directory to resolve a relative path against). The scheme is only meaningful with
``--remote``; on a local invocation it is an error.
"""

from __future__ import annotations

REMOTE_PATH_SCHEME = "remote:"


def has_remote_scheme(value: str) -> bool:
    """True if ``value`` carries the ``remote:`` scheme."""
    return value.startswith(REMOTE_PATH_SCHEME)


def remote_scheme_path(value: str) -> str:
    """Return the path part after ``remote:`` (the caller has checked the scheme)."""
    return value[len(REMOTE_PATH_SCHEME):]
