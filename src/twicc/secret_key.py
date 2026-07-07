"""Per-install Django SECRET_KEY, generated once and persisted in the data dir.

The repo cannot ship a usable SECRET_KEY (it would be public and shared by
every install), so each installation generates its own high-entropy key on
first startup and persists it to ``<data_dir>/secret-key`` (chmod 600). The
key must stay stable across restarts: rotating it logs every browser session
out and invalidates every signature derived from it — notably the MCP session
tokens (:mod:`twicc.mcp.identity`), whose whole point is to survive backend
restarts.

``TWICC_SECRET_KEY`` overrides the file for operators who manage the key
themselves; the data-dir ``.env`` is loaded before settings read it.
"""

import os
import secrets

from twicc import paths


def load_or_create_secret_key() -> str:
    """Return the install's SECRET_KEY, creating and persisting it on first use."""
    env_value = os.environ.get("TWICC_SECRET_KEY", "").strip()
    if env_value:
        return env_value
    path = paths.get_secret_key_path()
    try:
        key = path.read_text().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(50)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return key
