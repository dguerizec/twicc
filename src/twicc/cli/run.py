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

from twicc.instance_lock import InstanceAlreadyRunning, InstanceLock  # noqa: E402
from twicc.orchestrator import get_orchestrator_registry  # noqa: E402
from twicc.paths import get_data_dir  # noqa: E402
from twicc.pricing_task import start_price_sync_task, sync_all_providers  # noqa: E402
from twicc.search import SearchIndexLockedError, init_search_index, shutdown_search_index  # noqa: E402
from twicc.search_indexing_task import (  # noqa: E402
    get_active_indexing_tasks,
    kick_off_search_indexing,
    stop_search_index_task,
)
from twicc.version_check_task import start_version_check_task, stop_version_check_task  # noqa: E402
from twicc.tips_manifest import init_manifest, start_tips_watcher_task  # noqa: E402


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
    request_shutdown,
) -> None:
    """Coordinate the cross-provider parts of the search lifecycle.

    Initializes the global Tantivy index once every provider's initial
    sync has reported completion, then signals ``search_index_ready``
    so provider watchers can start writing to it. After every
    provider's background compute has reported completion, fires the
    global search-indexing task via :func:`kick_off_search_indexing`,
    which also registers the task handle into the module-level list
    consulted by the shutdown path.

    Hot-toggle re-triggers happen separately from this coroutine: the
    orchestrator registry schedules its own ``kick_off_search_indexing``
    after each ``start_one`` once the provider's ``compute_done`` fires,
    and the run lock inside the indexing module serializes those runs
    against this boot pass.

    ``shutdown_event`` short-circuits both gates so a server stopping
    mid-startup doesn't leave dangling work. ``request_shutdown`` is the
    callback used to stop the server when initialization fails fatally
    (currently: Tantivy writer lock already held by another process).
    """
    await orchestrators.wait_initial_sync_done()
    if shutdown_event.is_set():
        return

    try:
        await asyncio.to_thread(init_search_index)
    except SearchIndexLockedError as exc:
        logger.error(
            "Cannot start TwiCC: the search index writer lock at %s is already held.\n"
            "This usually means a previous TwiCC process did not shut down cleanly and a\n"
            "subprocess (typically the background compute worker) is still running.\n"
            "Run `pkill -f twicc` to clean up stale processes, then start TwiCC again.\n"
            "If the issue persists, identify the holder with:\n"
            "  lsof %s/.tantivy-writer.lock",
            exc.search_dir, exc.search_dir,
        )
        request_shutdown()
        return

    logger.info("Search index initialized (after every provider's initial sync)")
    search_index_ready.set()

    await orchestrators.wait_compute_done()
    if shutdown_event.is_set():
        return

    await kick_off_search_indexing()
    logger.info("Background search indexing started (after every provider's compute)")


async def run_server(port: int):
    """Run the ASGI server with all background tasks."""
    import signal

    import uvicorn

    from twicc.asgi import application

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    # Start the unified DB writer FIRST, before any producer that writes to
    # the DB. Every periodic task that lands a write — initial price sync
    # below, commands_task / usage_task / model_retirement_task spawned by
    # the orchestrators, and the per-provider initial sync + compute they
    # drive — routes its writes through this consumer's queues. Standing it
    # up before any of them guarantees the consumer always exists by the
    # time a producer tries to submit. ``stop_unified_consumer`` is used at
    # the very end of this function's shutdown path, after every producer
    # has shut down.
    from twicc.providers.db_writer import start_unified_consumer, stop_unified_consumer
    start_unified_consumer()

    # Cross-provider initial price sync runs *before* per-provider orchestrators
    # so they can rely on prices being in DB by the time their compute paths run.
    # A single OpenRouter fetch covers every provider that has declared an
    # ``OPENROUTER_MODEL_PREFIX``; failure here is logged and non-fatal. The
    # actual ``ModelPrice`` inserts flow through the unified consumer started
    # just above.
    await sync_all_providers()
    init_manifest()

    # When ``TWICC_AUTO_ENABLE_PROVIDERS=1`` (devctl worktree mode) seeds the
    # initial ``disabledProviders=[]`` choice if the file lacks one, so the
    # orchestrators below see every provider as enabled instead of the empty
    # set that gates the activation dialog. No-op once the user has made a
    # choice — toggles from Settings keep working normally.
    from twicc.providers.state import apply_auto_enable_providers_bootstrap
    apply_auto_enable_providers_bootstrap()

    # Cross-provider search lifecycle event: set once ``init_search_index()``
    # has returned, so provider watchers know they can write into the
    # index. Created here, owned by ``_orchestrate_global_search``,
    # awaited by every ``BaseOrchestrator.start`` that owns a watcher.
    search_index_ready = asyncio.Event()

    # Per-provider orchestrators (started in parallel; each one is
    # responsible for its own task graph and dependency ordering).
    orchestrators = get_orchestrator_registry()

    await orchestrators.start_all(shutdown_event, search_index_ready)

    # Configure uvicorn
    # log_config=None prevents Uvicorn from installing its own StreamHandlers;
    # uvicorn loggers are handled by Django's LOGGING config instead.
    # The server is created up front so the search-lifecycle coordinator can
    # request a graceful shutdown via ``request_shutdown`` if it fails fatally
    # (e.g. another process holds the Tantivy writer lock).
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        """Trigger a graceful shutdown of every component.

        Used both by the OS signal handler and by background coroutines
        that encounter a non-recoverable startup error.
        """
        # Cooperative stop for any provider's blocking sync threads
        # (async tasks listen for ``shutdown_event`` directly).
        orchestrators.request_thread_stop_all()
        shutdown_event.set()
        server.should_exit = True

    # Cross-provider search-lifecycle coordinator. Runs in parallel to
    # the server so ``init_search_index`` doesn't gate uvicorn startup.
    # The background search-indexing task it spawns (and any hot-toggle
    # re-trigger) is tracked via ``get_active_indexing_tasks`` so we
    # can stop every live run cleanly below.
    search_orchestrator_task = asyncio.create_task(
        _orchestrate_global_search(orchestrators, shutdown_event, search_index_ready, request_shutdown)
    )

    # Cross-provider periodic tasks
    price_sync_task = asyncio.create_task(start_price_sync_task(shutdown_event))
    version_check_task = asyncio.create_task(start_version_check_task())
    # Dev-only: re-scan the tips dir every 10 s and broadcast on change.
    # The task short-circuits to a no-op outside TWICC_DEBUG so this is a
    # zero-cost coroutine in production.
    tips_watcher_task = asyncio.create_task(start_tips_watcher_task(shutdown_event))

    # CLI session-create plumbing (cf. docs/superpowers/specs/2026-05-17-cli-session-create-design.md)
    from twicc.heartbeat import heartbeat_loop
    from twicc.pending_sessions_watcher import get_pending_sessions_watcher

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    pending_watcher_task = asyncio.create_task(get_pending_sessions_watcher().start())

    def handle_signal(signum, frame):
        logger.info("Received signal %s, initiating shutdown...", signum)
        request_shutdown()

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

        # Tips watcher exits cleanly when shutdown_event fires (set above),
        # but we still cancel it explicitly to cover the no-op TWICC_DEBUG=
        # off path (coroutine already returned) and any awaited wait_for.
        logger.info("Stopping tips watcher task...")
        await _cancel_task(tips_watcher_task, "Tips watcher task")

        logger.info("Stopping heartbeat task...")
        await _cancel_task(heartbeat_task, "Heartbeat task")

        logger.info("Stopping pending-sessions watcher task...")
        await _cancel_task(pending_watcher_task, "Pending-sessions watcher task")

        # Stop the global search-indexing task(s) (if any ever started)
        # and the coordinator that gated them. Order matters: cancel the
        # coordinator first so it doesn't spawn a new search task after
        # we've already stopped the running ones. The active list covers
        # both the boot pass and any hot-toggle re-trigger queued behind
        # it on the run lock.
        await _cancel_task(search_orchestrator_task, "Search lifecycle coordinator")
        active_indexing_tasks = get_active_indexing_tasks()
        if active_indexing_tasks:
            logger.info(
                "Stopping search index task(s) (%d active)...",
                len(active_indexing_tasks),
            )
            stop_search_index_task()
            for idx, task in enumerate(active_indexing_tasks, start=1):
                await _cancel_task(task, f"Search index task #{idx}")
        else:
            logger.info("Search index task was not started, skipping")

        # Then let every provider tear down its own tasks (in parallel).
        await orchestrators.shutdown_all()

        # Stop the unified DB writer. Done after every orchestrator has shut
        # down — their blocking shutdown() guarantees no producer thread or
        # subprocess is still alive, so nothing is left pushing onto the
        # shared queues.
        await stop_unified_consumer()

        # Finally tear down the search index itself. Done after the
        # providers' watchers are stopped so no late write races us.
        logger.info("Shutting down search index...")
        await asyncio.to_thread(shutdown_search_index)

        logger.info("Server shutdown complete")


def main():
    # Acquire the per-data-dir instance lock before doing anything that
    # touches state shared by every TwiCC process (DB migrations, Tantivy
    # writer, ports). The lock is a POSIX flock on <data_dir>/twicc.lock,
    # released automatically by the kernel on any kind of process death
    # (including SIGKILL or crash) — no stale-lock cleanup needed.
    instance_lock = InstanceLock(get_data_dir())
    try:
        instance_lock.acquire()
    except InstanceAlreadyRunning as exc:
        logger.error("%s", exc)
        sys.exit(1)

    try:
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

        logger.info("Server starting on http://localhost:%d", port_int)

        # Now that the port is known, write the sidecar info file so a second
        # ``twicc`` invocation can show a helpful "Holder: PID X, port Y" line.
        instance_lock.write_info(port=port_int)

        # Remove the startup console handler -- from now on, only the file handler remains
        logging.getLogger("twicc").removeHandler(_startup_console)

        # Run async server (initial sync runs as an async task inside run_server)
        asyncio.run(run_server(port_int))
    finally:
        instance_lock.release()
