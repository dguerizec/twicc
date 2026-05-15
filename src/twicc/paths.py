"""
Centralized path resolution for TwiCC data directories.

All data (database, logs, config) lives in a single "data directory":
- Default: ~/.twicc/
- Override: TWICC_DATA_DIR environment variable

Structure:
    <data_dir>/
    ├── .env              # Infrastructure config (ports, password hash, etc.)
    ├── settings.json     # User preferences synced across devices
    ├── db/
    │   └── data.sqlite (+shm, +wal)
    ├── logs/
    │   ├── backend.log              # Backend application logs
    │   ├── frontend.log             # Frontend (Vite) process output
    │   └── sdk/
    │       ├── claude_code/
    │       │   └── {session_id}.jsonl   # Raw Claude Code SDK wire messages
    │       └── codex/
    │           └── {session_id}.jsonl   # Raw Codex SDK stream events + approvals
    └── search-index/
        └── (tantivy index files)

In development with worktrees, devctl.py sets TWICC_DATA_DIR to the
worktree root so each worktree gets its own DB, logs, and .env.
"""

import os
import re
from pathlib import Path

# Environment variable name to override the data directory
TWICC_DATA_DIR_ENV = "TWICC_DATA_DIR"

# Default data directory (same pattern as ~/.claude/)
DEFAULT_DATA_DIR = Path.home() / ".twicc"


def get_data_dir() -> Path:
    """Return the resolved data directory.

    Priority:
    1. TWICC_DATA_DIR environment variable (if set and non-empty)
    2. ~/.twicc/ (default)
    """
    env_value = os.environ.get(TWICC_DATA_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value).resolve()
    return DEFAULT_DATA_DIR


def get_db_dir() -> Path:
    """Return the database directory (<data_dir>/db/)."""
    return get_data_dir() / "db"


def get_db_path() -> Path:
    """Return the database file path (<data_dir>/db/data.sqlite)."""
    return get_db_dir() / "data.sqlite"


def get_logs_dir() -> Path:
    """Return the logs directory (<data_dir>/logs/)."""
    return get_data_dir() / "logs"


def get_sdk_logs_dir(provider: str | None = None) -> Path:
    """Return the SDK logs directory.

    Without ``provider``, returns the parent ``<data_dir>/logs/sdk/``
    (used by ``ensure_data_dirs`` and any cross-provider tooling).
    With a provider value (e.g. ``Provider.CLAUDE_CODE.value`` /
    ``Provider.CODEX.value``), returns the per-provider subdirectory
    where each provider's logger writes ``{session_id}.jsonl``.

    A string is taken (rather than the ``Provider`` enum) to keep
    ``paths.py`` free of any ``twicc.core`` import — this module is
    intentionally low-level and gets imported from many places.
    """
    base = get_logs_dir() / "sdk"
    if provider is None:
        return base
    return base / provider


def get_backend_log_path() -> Path:
    """Return the backend log file path (<data_dir>/logs/backend.log)."""
    return get_logs_dir() / "backend.log"


def get_frontend_log_path() -> Path:
    """Return the frontend log file path (<data_dir>/logs/frontend.log)."""
    return get_logs_dir() / "frontend.log"


def get_env_path() -> Path:
    """Return the .env file path (<data_dir>/.env)."""
    return get_data_dir() / ".env"


def get_synced_settings_path() -> Path:
    """Return the synced settings file path (<data_dir>/settings.json)."""
    return get_data_dir() / "settings.json"


def get_search_dir() -> Path:
    """Return the search index directory (<data_dir>/search-index/)."""
    return get_data_dir() / "search-index"


def get_terminal_config_path() -> Path:
    return get_data_dir() / "terminal-config.json"


def get_message_snippets_config_path() -> Path:
    return get_data_dir() / "message-snippets.json"


def get_workspaces_path() -> Path:
    """Path to the workspaces definition file."""
    return get_data_dir() / "workspaces.json"


def path_to_project_id(path: str) -> str:
    """Convert a filesystem path to a TwiCC project ID.

    A project in TwiCC is a working directory; its ID is derived from the
    directory path by replacing every non-alphanumeric character with a
    dash. The convention is inherited from Claude Code (which names its
    own subfolders of ``~/.claude/projects/`` the same way), but TwiCC
    reuses it as the cross-provider project key — multiple providers can
    run inside the same project, sharing the same ID.

    Pure function: the caller is responsible for resolving and
    normalizing the path beforehand (e.g. via ``os.path.realpath``) if a
    canonical ID is required.
    """
    return re.sub(r'[^a-zA-Z0-9]', '-', path)


def _migrate_legacy_data_files() -> None:
    """Rename legacy data files left over from older TwiCC versions.

    The agent settings presets file used to live at
    ``<data_dir>/claude-settings-presets.json`` (no provider prefix). The
    cross-provider naming scheme is now ``<data_dir>/<provider>-settings-presets.json``,
    owned by ``BaseProviderHelpers.get_settings_presets_path``. This
    migration is Claude Code-specific by design: it predates the
    multi-provider split and only ever targeted the Claude Code file.
    """
    legacy_presets = get_data_dir() / "claude-settings-presets.json"
    new_presets = get_data_dir() / "claude_code-settings-presets.json"
    if legacy_presets.exists() and not new_presets.exists():
        legacy_presets.rename(new_presets)


def ensure_data_dirs() -> None:
    """Create the data directory structure if it doesn't exist."""
    get_db_dir().mkdir(parents=True, exist_ok=True)
    get_sdk_logs_dir().mkdir(parents=True, exist_ok=True)
    get_search_dir().mkdir(parents=True, exist_ok=True)
    _migrate_legacy_data_files()
