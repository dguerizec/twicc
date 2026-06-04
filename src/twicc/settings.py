import os
import shlex
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from twicc.paths import ensure_data_dirs, get_backend_log_path, get_db_path, get_env_path
from twicc.version import get_version

PACKAGE_DIR = Path(__file__).resolve().parent  # src/twicc/

APP_VERSION = get_version()
DEV_MODE = PACKAGE_DIR.parent.name == "src"


def _is_uv_managed(segment: str) -> bool:
    """Whether ``sys.executable`` lives inside a uv-managed directory of
    the given kind: ``archive-v0`` for ephemeral ``uvx`` runs (cache),
    ``tools`` for persistent ``uv tool install`` venvs.

    We don't rely on environment variables: ``UV_RUN_RECURSION_DEPTH`` is
    only set by ``uv run``, never by ``uvx`` / ``uv tool run``. The
    interpreter path is the only stable signal across both modes.

    We intentionally do NOT ``resolve()`` the path: uv venvs ship a
    ``bin/python`` that's a symlink to the system Python (or to its own
    download), so resolving would strip the very ``uv/<segment>/`` parts
    we're trying to detect.
    """
    parts = Path(sys.executable).parts
    for i, p in enumerate(parts):
        if p == "uv" and i + 1 < len(parts) and parts[i + 1] == segment:
            return True
    return False


UVX_MODE = not DEV_MODE and _is_uv_managed("archive-v0")


def _resolve_twicc_launch_prefix() -> str:
    """
    Build the shell prefix that re-invokes the same TwiCC distribution as
    a command, ready to be suffixed with subcommand args (e.g.
    ``claude auth login``).

    Detection, in order:

    1. ``uvx twicc`` when launched via ephemeral ``uvx`` (cache lives in
       ``~/.cache/uv/archive-v0/<hash>/``).
    2. Bare ``twicc`` when launched from a ``uv tool install`` venv
       (``~/.local/share/uv/tools/<name>/``) AND its shim is on PATH.
    3. ``uv run --directory <dir> <script>`` when launched via ``uv run``
       in dev mode (e.g. ``uv run ./run.py`` via ``devctl``). The
       ``--directory`` makes the command work regardless of the
       terminal's current working directory.
    4. Quoted absolute path of the ``twicc`` script when ``sys.argv[0]``
       points at one (covers the ``uv tool install`` shim-not-on-PATH
       case, and any other installed-package layout).
    5. ``<sys.executable> -m twicc`` as a generic fallback (covers
       ``python -m twicc`` and any other case where the launching
       interpreter has the ``twicc`` package importable).
    """
    if UVX_MODE:
        return "uvx twicc"

    # ``uv tool install twicc`` puts the venv in ``~/.local/share/uv/tools/twicc/``
    # and creates a shim in the uv tool bin dir. Only return the bare name when
    # that shim is actually on PATH (so the command works from anywhere);
    # otherwise fall through to the absolute argv0 path below.
    if not DEV_MODE and _is_uv_managed("tools") and shutil.which("twicc"):
        return "twicc"

    # Build the shortest viable form of argv0 — both ``absolute()`` (no
    # symlink following, keeps the path the user actually typed) and
    # ``resolve()`` (follows symlinks, but normalises ``..``/``.``) are
    # candidates; we keep whichever is shorter. In practice ``absolute()``
    # almost always wins (e.g. ``~/.local/bin/twicc`` vs the longer
    # ``~/.local/share/uv/tools/twicc/bin/twicc`` target), but ``resolve()``
    # can win when argv0 carries redundant segments.
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidates = [Path(argv0).absolute()]
        try:
            candidates.append(Path(argv0).resolve())
        except OSError:
            pass
        argv0_path = min(candidates, key=lambda p: len(str(p)))
    else:
        argv0_path = None

    # uv run <script> in dev mode: re-invoke the same script through uv,
    # forcing the project dir via ``--directory`` so the prefix stays a
    # single shell command (no ``&&``). The script is named bare (no ``./``)
    # so uv resolves it against the ``--directory`` target, not the caller's
    # cwd at invocation time.
    is_uv_run = "UV_RUN_RECURSION_DEPTH" in os.environ
    if is_uv_run and DEV_MODE and argv0_path and argv0_path.is_file():
        return (
            f"uv run --directory {shlex.quote(str(argv0_path.parent))} "
            f"{argv0_path.name}"
        )

    # Installed ``twicc`` script.
    if argv0_path and argv0_path.is_file() and argv0_path.name in ("twicc", "twicc.exe"):
        return shlex.quote(str(argv0_path))

    # Sibling entry-point script in the same venv ``bin/`` as ``sys.executable``.
    # Covers any installed-package layout (including ``uv tool install`` with
    # the shim off PATH) — always preferable to the ``python -m twicc``
    # fallback because it doesn't depend on PATH and produces a single-binary
    # command.
    sibling = Path(sys.executable).parent / "twicc"
    if sibling.is_file():
        return shlex.quote(str(sibling))

    # Fallback: same interpreter, twicc as a module.
    return f"{shlex.quote(sys.executable)} -m twicc"


# Shell prefix exposed via the bootstrap API so the frontend can render
# accurate "run <X> ..." instructions and inject them into the terminal.
TWICC_LAUNCH_PREFIX = _resolve_twicc_launch_prefix()

# Surface the same prefix as TWICC_BIN so subprocesses (Claude/Codex
# agents, and the shell commands they spawn from skills) can re-invoke
# THIS TwiCC instance reliably — independent of whatever ``twicc`` may
# happen to be first in PATH on the user's system. The plugin's
# SKILL.md files all read TWICC_BIN with a ``command -v twicc`` fallback.
os.environ.setdefault("TWICC_BIN", TWICC_LAUNCH_PREFIX)

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env)
# Idempotent: no-op if already loaded by run.py
load_dotenv(get_env_path())

# Ensure data directories exist (db/, logs/)
ensure_data_dirs()

SECRET_KEY = "dev-insecure-key-do-not-use-in-production"

# TWICC_DEBUG is set by devctl when launching the backend process.
# It controls Django's DEBUG mode and the twicc logger level.
DEBUG = os.environ.get("TWICC_DEBUG", "").strip().lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "channels",
    "twicc.core.apps.CoreConfig",
]

# In debug mode, try to load django-extensions (dev dependency, not required at runtime)
if DEBUG:
    try:
        import django_extensions  # noqa: F401

        INSTALLED_APPS.insert(-1, "django_extensions")
    except ImportError:
        pass

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "twicc.auth.middleware.PasswordAuthMiddleware",
    "twicc.auth.middleware.RpcTokenAuthMiddleware",
]

# Password protection
# Set TWICC_PASSWORD_HASH in .env to enable password protection.
# Use `twicc password set` (or `uvx twicc password set`, etc.) to set it
# interactively. If not set or empty, the app is accessible without authentication.
TWICC_PASSWORD_HASH = os.environ.get("TWICC_PASSWORD_HASH", "")

# Session settings
SESSION_COOKIE_NAME = os.environ.get("TWICC_SESSION_COOKIE", "sessionid")
SESSION_ENGINE = "django.contrib.sessions.backends.db"
# Effectively no server-side expiry: TwiCC is self-hosted, the real protection
# is the password (PBKDF2) plus the fingerprint that invalidates every session
# the moment TWICC_PASSWORD_HASH changes (see auth.session_auth). Sliding expiry
# adds no meaningful security here. Browsers cap Set-Cookie Max-Age at ~400 days
# regardless, so users may need to re-login about once a year — that's the
# floor we can't push past from the server side.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365 * 100  # 100 years (browsers cap ~400d)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# SessionMiddleware would otherwise emit an UPDATE django_session on every
# authenticated request to refresh expire_date — concurrent with our DB writer
# / watcher writes since it runs outside run_under_db_write_lock. With
# SESSION_COOKIE_AGE at 100 years there is nothing useful to refresh anyway.
SESSION_SAVE_EVERY_REQUEST = False

ROOT_URLCONF = "twicc.urls"

ASGI_APPLICATION = "twicc.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": get_db_path(),
        "OPTIONS": {
            "timeout": 30,
            "init_command": """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=30000;
                PRAGMA mmap_size=134217728;
                PRAGMA journal_size_limit=27103364;
                PRAGMA cache_size=2000;
            """,
        },
    }
}

# Frontend build directory
# Built frontend assets live inside the package: src/twicc/static/frontend/
# This path works both in dev (after npm run build) and when installed via pip/uvx.
# Static file serving is handled by BlackNoise at the ASGI level (see asgi.py).
FRONTEND_DIST_DIR = PACKAGE_DIR / "static" / "frontend"

# Logging configuration
# All logs go to file (<data_dir>/logs/backend.log)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "provider": {
            "()": "twicc.logging_context.ProviderFilter",
        },
    },
    "formatters": {
        "standard": {
            "format": "[{asctime} - {levelname:>6} - {provider:>11} - {name}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": str(get_backend_log_path()),
            "formatter": "standard",
            "encoding": "utf-8",
            "filters": ["provider"],
        },
    },
    "loggers": {
        "twicc": {
            "handlers": ["file"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Display levels computation — one constant per provider, accessed by the
# provider helpers via ``BaseProviderHelpers.current_compute_version``.
# Bump the relevant constant when the corresponding provider's parsing/compute
# rules change to trigger recomputation. ``None`` declares "no compute pipeline
# yet" — sessions of that provider are reported up-to-date as-is.
CLAUDE_CODE_COMPUTE_VERSION = 97
CODEX_COMPUTE_VERSION = 29

# Search index version
# Bumped when the schema or document layout changes — forces a full
# rebuild of the on-disk index at next startup.
# v2 -> v3: added `hidden` and `spawned_by` fields (hidden-sessions feature).
# v3 -> v4: added `spawn_root` field for full-tree filiation queries.
CURRENT_SEARCH_VERSION = 4

# Process auto-stop timeouts (in seconds)
# Processes are automatically stopped if they remain in a state for too long
PROCESS_TIMEOUT_STARTING = 60  # 1 minute - process stuck during startup
PROCESS_TIMEOUT_USER_TURN = 15 * 60  # 15 minutes - idle, waiting for user input
PROCESS_TIMEOUT_ASSISTANT_TURN = 2 * 60 * 60  # 2 hours - no activity from agent
PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE = 6 * 60 * 60  # 6 hours - max total duration for a turn

# Cron auto-restart
# Set TWICC_NO_CRON_RESTART=1 to disable automatic restart of cron jobs,
# both at startup (for sessions with persisted crons) and when recurring crons expire.
CRON_AUTO_RESTART = os.environ.get("TWICC_NO_CRON_RESTART", "").strip().lower() not in ("1", "true", "yes")

# Codex plugin install
# Set TWICC_NO_CODEX_PLUGIN=1 to skip ``ensure_twicc_plugin_installed`` at Codex
# orchestrator start. ``~/.codex/config.toml`` is global and already managed by
# the main install — worktrees set this flag so they don't race on it.
CODEX_PLUGIN_INSTALL_ENABLED = os.environ.get("TWICC_NO_CODEX_PLUGIN", "").strip().lower() not in ("1", "true", "yes")

# Auto-enable every registered provider at first boot
# Set TWICC_AUTO_ENABLE_PROVIDERS=1 to bypass the initial provider activation
# dialog: at backend startup, if ``disabledProviders`` is absent from
# settings.json, ``apply_auto_enable_providers_bootstrap()`` writes an empty
# list (equivalent to validating the dialog with every provider checked).
# Idempotent once the key exists, so user toggles from Settings are preserved.
# Used by devctl in worktree mode so dev servers come up without prompting.
AUTO_ENABLE_PROVIDERS = os.environ.get("TWICC_AUTO_ENABLE_PROVIDERS", "").strip().lower() in ("1", "true", "yes")
