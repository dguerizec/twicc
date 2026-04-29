"""
CLI entry point for the TWICC application.

Handles Django setup, migrations, and starts the server. All Claude Code
provider tasks (sync, watcher, compute, pricing, auth, usage, statuspage,
slash commands, search index, model retirement, cron restart, process
manager) are owned by ``ClaudeCodeOrchestrator``. The CLI only manages the
HTTP server lifecycle and cross-provider tasks (e.g. PyPI version check).

Used by:
- ``uvx twicc`` / ``pip install twicc && twicc``  (via project.scripts)
- ``python -m twicc``  (via __main__.py)
- ``uv run run.py``  (dev wrapper at repo root)
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from twicc.providers.claude_code.env import purge_claude_code_vars
from twicc.paths import get_env_path

# Clean up Claude Code environment variables that may have been inherited from a
# parent process (e.g., when devctl or TwiCC is launched from within Claude Code).
# These variables cause Claude Code to think it's already running inside an SDK
# session, preventing interactive use from TwiCC's terminal.
purge_claude_code_vars(os.environ)

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env)
load_dotenv(get_env_path())

# Configure Django before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

import django  # noqa: E402

django.setup()

# Logger must be created AFTER django.setup() so LOGGING config is applied
logger = logging.getLogger("twicc.run")

# Add a temporary console handler for startup messages (just the text, no timestamp/level).
# It will be removed once the server is about to start, so only the file handler remains.
_startup_console = logging.StreamHandler()
_startup_console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("twicc").addHandler(_startup_console)

# Now we can import Django-dependent modules
from django.core.management import call_command  # noqa: E402

from twicc.providers.claude_code.orchestrator import ClaudeCodeOrchestrator  # noqa: E402
from twicc.version_check_task import start_version_check_task, stop_version_check_task  # noqa: E402


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    """Cancel an asyncio task and wait for it to finish."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


async def run_server(port: int):
    """Run the ASGI server with all background tasks."""
    import signal

    import uvicorn

    from twicc.asgi import application

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    # Claude Code provider owns its own tasks
    claude_code = ClaudeCodeOrchestrator()
    await claude_code.start(shutdown_event)

    # Cross-provider tasks
    version_check_task = asyncio.create_task(start_version_check_task())

    # Configure uvicorn
    # log_config=None prevents Uvicorn from installing its own StreamHandlers;
    # uvicorn loggers are handled by Django's LOGGING config instead.
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)

    def handle_signal(signum, frame):
        logger.info("Received signal %s, initiating shutdown...", signum)
        claude_code.request_stop()
        shutdown_event.set()
        server.should_exit = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        await server.serve()
    finally:
        logger.info("Server shutdown initiated...")

        # Stop cross-provider tasks first
        logger.info("Stopping version check task...")
        stop_version_check_task()
        await _cancel_task(version_check_task, "Version check task")

        # Then let the Claude Code provider tear down its own tasks
        await claude_code.shutdown()

        logger.info("Server shutdown complete")


def main():
    logger.info("TWICC starting...")
    logger.info("Environment loaded")

    from django.conf import settings as django_settings
    logger.info("TwiCC launch prefix: %s", django_settings.TWICC_LAUNCH_PREFIX)

    # Migrations auto
    call_command("migrate", verbosity=0)
    logger.info("Migrations applied")

    # The auth_task handles Claude CLI authentication detection: it logs
    # the current state and broadcasts it to connected clients. Sending
    # messages is disabled in the UI when not authenticated.

    # Parse port
    port = os.environ.get("TWICC_PORT", "3500")
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError()
    except ValueError:
        logger.error("Invalid port '%s'. Must be a number between 1 and 65535.", port)
        sys.exit(1)

    logger.info("Server starting on http://0.0.0.0:%d", port_int)

    # Remove the startup console handler -- from now on, only the file handler remains
    logging.getLogger("twicc").removeHandler(_startup_console)

    # Run async server (initial sync runs as an async task inside run_server)
    asyncio.run(run_server(port_int))
