"""
CLI entry point for the TWICC application.

Handles Django setup, migrations, and starts the server. Each provider's
own background tasks (sync, watcher, compute, auth, usage, ...) are
owned by its :class:`BaseOrchestrator` subclass; the CLI just iterates
the :class:`OrchestratorRegistry` to start, signal, and shut them down.

The CLI itself owns the cross-provider tasks:
- PyPI version check
- OpenRouter price sync (one fetch shared across every provider that
  has declared an ``OPENROUTER_MODEL_PREFIX``)
- Tantivy search index lifecycle (``init_search_index`` /
  ``shutdown_search_index``) and the startup search-indexing task,
  gated on every provider's initial-sync / compute completion via the
  events on :class:`BaseOrchestrator`.

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

from twicc.paths import get_env_path

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env)
load_dotenv(get_env_path())

# Configure Django before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

import django  # noqa: E402

django.setup()

# Clean up provider-specific environment variables that may have been
# inherited from a parent process (e.g. ``CLAUDE_CODE_*`` when TwiCC is
# launched from within Claude Code). These would make subprocesses we
# spawn (login shell, tmux, the provider CLI itself) think they are
# already inside an SDK session. Each provider's helper purges its own
# markers; ordering after ``django.setup()`` is required because the
# helpers registry instantiates provider helpers that touch Django
# models on import. None of the variables we strip influence anything
# Django reads at startup, so the move is benign.
from twicc.providers.helpers import get_provider_helpers_registry  # noqa: E402

get_provider_helpers_registry().purge_env_vars(os.environ)

# Logger must be created AFTER django.setup() so LOGGING config is applied
logger = logging.getLogger("twicc.run")

# Add a temporary console handler for startup messages (just the text, no timestamp/level).
# It will be removed once the server is about to start, so only the file handler remains.
_startup_console = logging.StreamHandler()
_startup_console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("twicc").addHandler(_startup_console)

# Now we can import Django-dependent modules
from django.core.management import call_command  # noqa: E402

from twicc.orchestrator import get_orchestrator_registry  # noqa: E402
from twicc.pricing_task import start_price_sync_task, sync_all_providers  # noqa: E402
from twicc.search import init_search_index, shutdown_search_index  # noqa: E402
from twicc.search_indexing_task import start_search_index_task, stop_search_index_task  # noqa: E402
from twicc.version_check_task import start_version_check_task, stop_version_check_task  # noqa: E402


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    """Cancel an asyncio task and wait for it to finish."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


async def _orchestrate_global_search(
    orchestrators,
    shutdown_event: asyncio.Event,
    search_index_ready: asyncio.Event,
    state: dict,
) -> None:
    """Coordinate the cross-provider parts of the search lifecycle.

    Initializes the global Tantivy index once every provider's initial
    sync has reported completion, then signals ``search_index_ready``
    so provider watchers can start writing to it. After every
    provider's background compute has reported completion, fires the
    global search-indexing task.

    The ``state`` dict is the bridge back to ``run_server``: when this
    coroutine creates the search-indexing task, it stores the handle
    under ``state["search_indexing_task"]`` so ``run_server`` can stop
    it cleanly during shutdown. ``shutdown_event`` short-circuits both
    gates so a server stopping mid-startup doesn't leave dangling work.
    """
    await orchestrators.wait_initial_sync_done()
    if shutdown_event.is_set():
        return

    await asyncio.to_thread(init_search_index)
    logger.info("Search index initialized (after every provider's initial sync)")
    search_index_ready.set()

    await orchestrators.wait_compute_done()
    if shutdown_event.is_set():
        return

    state["search_indexing_task"] = asyncio.create_task(start_search_index_task())
    logger.info("Background search indexing started (after every provider's compute)")


async def run_server(port: int):
    """Run the ASGI server with all background tasks."""
    import signal

    import uvicorn

    from twicc.asgi import application

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    # Cross-provider initial price sync runs *before* per-provider orchestrators
    # so they can rely on prices being in DB by the time their compute paths run.
    # A single OpenRouter fetch covers every provider that has declared an
    # ``OPENROUTER_MODEL_PREFIX``; failure here is logged and non-fatal.
    await sync_all_providers()

    # Cross-provider search lifecycle event: set once ``init_search_index()``
    # has returned, so provider watchers know they can write into the
    # index. Created here, owned by ``_orchestrate_global_search``,
    # awaited by every ``BaseOrchestrator.start`` that owns a watcher.
    search_index_ready = asyncio.Event()

    # Per-provider orchestrators (started in parallel; each one is
    # responsible for its own task graph and dependency ordering).
    orchestrators = get_orchestrator_registry()
    await orchestrators.start_all(shutdown_event, search_index_ready)

    # Cross-provider search-lifecycle coordinator. Runs in parallel to
    # the server so ``init_search_index`` doesn't gate uvicorn startup.
    # Stores its background ``search_indexing_task`` handle into the
    # ``search_state`` dict once compute finishes, so we can stop it
    # cleanly below.
    search_state: dict = {"search_indexing_task": None}
    search_orchestrator_task = asyncio.create_task(
        _orchestrate_global_search(orchestrators, shutdown_event, search_index_ready, search_state)
    )

    # Cross-provider periodic tasks
    price_sync_task = asyncio.create_task(start_price_sync_task(shutdown_event))
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
        # Cooperative stop for any provider's blocking sync threads
        # (async tasks listen for ``shutdown_event`` directly).
        orchestrators.request_thread_stop_all()
        shutdown_event.set()
        server.should_exit = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        await server.serve()
    finally:
        logger.info("Server shutdown initiated...")

        # Stop cross-provider tasks first. The price sync loop watches
        # ``shutdown_event`` directly (set above by the signal handler),
        # so we just wait for it to finish.
        logger.info("Stopping price sync task...")
        await _cancel_task(price_sync_task, "Price sync task")

        logger.info("Stopping version check task...")
        stop_version_check_task()
        await _cancel_task(version_check_task, "Version check task")

        # Stop the global search-indexing task (if it ever started) and
        # the coordinator that gated it. Order matters: cancel the
        # coordinator first so it doesn't spawn a new search task after
        # we've already stopped the running one.
        await _cancel_task(search_orchestrator_task, "Search lifecycle coordinator")
        search_indexing_task = search_state["search_indexing_task"]
        if search_indexing_task is not None:
            logger.info("Stopping search index task...")
            stop_search_index_task()
            await _cancel_task(search_indexing_task, "Search index task")
        else:
            logger.info("Search index task was not started, skipping")

        # Then let every provider tear down its own tasks (in parallel).
        await orchestrators.shutdown_all()

        # Finally tear down the search index itself. Done after the
        # providers' watchers are stopped so no late write races us.
        logger.info("Shutting down search index...")
        await asyncio.to_thread(shutdown_search_index)

        logger.info("Server shutdown complete")


def main():
    logger.info("TWICC starting...")
    logger.info("Environment loaded")

    from django.conf import settings as django_settings
    logger.info("TwiCC launch prefix: %s", django_settings.TWICC_LAUNCH_PREFIX)

    # Migrations auto
    call_command("migrate", verbosity=0)
    logger.info("Migrations applied")

    # Each provider's auth_task handles CLI authentication detection: it logs
    # the current state and broadcasts it to connected clients. Sending
    # messages is disabled in the UI when the owning provider is not
    # authenticated.

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
