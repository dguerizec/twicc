"""
Raw Codex SDK event logger.

Captures everything the Codex SDK pushes into our Python code:

- Stream notifications received from ``turn_handle.stream()`` (the live
  ``item/started`` / ``item/agentMessage/delta`` /
  ``item/reasoning/summaryPartAdded`` / ``item/reasoning/summaryTextDelta``
  / ``item/completed`` / … flow). These are strictly more granular than
  what lands in the rollout JSONL — deltas in particular are streaming-only.
- Approval requests the SDK calls back to ``CodexAgent._sync_approval_handler``
  via its worker thread, AND the response we hand back to the SDK.

Unlike Claude Code, Codex doesn't need a monkey-patch: the SDK is shaped
around explicit Python entry points (the agent iterates ``stream()`` itself,
and the bridge owns the ``_approval_handler`` slot), so the agent calls
into this module from those sites directly.

Each session gets its own log file:
``<data_dir>/logs/sdk/codex/{session_id}.jsonl``

Format mirrors the Claude Code logger so both providers can be grepped
with the same tooling::

    {"direction": "sent"|"received", "timestamp": ISO 8601, "data": {...}}

Active only when ``TWICC_DEBUG`` is set (set by devctl, never in production).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import orjson

from twicc.core.enums import Provider
from twicc.paths import get_sdk_logs_dir

logger = logging.getLogger(__name__)

# Resolve once at import time. Mirrors the Claude Code logger's gating:
# devctl injects TWICC_DEBUG=1 into the backend process; nothing else does.
SDK_LOGGING_ENABLED = os.environ.get("TWICC_DEBUG", "").strip().lower() in ("1", "true", "yes")

LOGS_DIR = get_sdk_logs_dir(Provider.CODEX.value)


def _get_log_path(session_id: str) -> Path:
    """Return the log file path for a given session."""
    return LOGS_DIR / f"{session_id}.jsonl"


def _write_log_line(session_id: str, direction: str, data: Any) -> None:
    """Append a single JSON line to the session's log file.

    Synchronous on purpose: per-line I/O is short and the call sites are
    already on the asyncio loop's hot path or in a worker thread (for
    approvals) — adding aiofile machinery would buy nothing.
    """
    if not SDK_LOGGING_ENABLED:
        return
    log_path = _get_log_path(session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "direction": direction,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": data,
    }
    try:
        with open(log_path, "ab") as f:
            f.write(orjson.dumps(line, default=str) + b"\n")
    except Exception:
        logger.exception("Failed to write Codex SDK log line to %s", log_path)


def _dump_payload(payload: Any) -> Any:
    """Convert a Pydantic model (or anything else) to a JSON-safe value.

    Codex SDK notifications and item payloads are Pydantic models.
    ``model_dump(mode="json", by_alias=True)`` preserves the wire camelCase
    so the log mirrors the on-the-wire shape rather than the Python
    snake_case projection.
    """
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json", by_alias=True)
    return payload


def log_stream_event(session_id: str, event: Any) -> None:
    """Log one notification received from ``turn_handle.stream()``.

    ``event`` is a Codex SDK envelope with ``method`` (str) and ``payload``
    (Pydantic model). Both are dumped, preserving the wire shape via
    ``by_alias=True``.
    """
    if not SDK_LOGGING_ENABLED:
        return
    _write_log_line(
        session_id,
        "received",
        {
            "method": getattr(event, "method", None),
            "payload": _dump_payload(getattr(event, "payload", None)),
        },
    )


def log_approval_request(session_id: str, method: str, params: dict | None) -> None:
    """Log an approval request the SDK pushed to ``_sync_approval_handler``.

    Called from the SDK's worker thread (``asyncio.to_thread`` from inside
    the SDK), not the asyncio loop. ``_write_log_line`` is sync so this is
    safe — no event loop interaction.
    """
    if not SDK_LOGGING_ENABLED:
        return
    _write_log_line(
        session_id,
        "received",
        {"method": method, "params": params},
    )


def log_approval_response(session_id: str, method: str, response: dict) -> None:
    """Log the response we hand back to the SDK for a given approval.

    Logged regardless of whether the response came from our own
    ``_async_approval_handler`` (a real user decision) or from a
    ``default_response_for(method)`` fallback (cancellation, bridge crash,
    early-loop call). The fallback path is exactly the kind of behaviour
    debug logs need to surface.
    """
    if not SDK_LOGGING_ENABLED:
        return
    _write_log_line(
        session_id,
        "sent",
        {"method": method, "response": response},
    )


def attach_stderr_logging(session_id: str, codex: Any) -> Callable[[str], None] | None:
    """Persist the ``codex app-server`` subprocess stderr to a per-session file.

    The SDK drains stderr into an in-memory ring buffer
    (``_stderr_lines``, 400 lines) that is lost with the process — useless
    for post-mortem debugging of the Rust side (tracing output selected via
    ``RUST_LOG`` goes to stderr). When debug logging is on, replace the
    SDK's drain-thread starter with a tee variant writing every line to
    ``<data_dir>/logs/sdk/codex/{session_id}-stderr.log`` while still
    feeding the ring buffer.

    Must be called BEFORE the first RPC (the subprocess — and its drain
    thread — starts lazily on ``_ensure_initialized``). No-op in production.

    Because it runs before ``thread_start``, ``session_id`` here is the
    frontend *draft* id — Codex hasn't minted the canonical thread id yet.
    Returns a ``rebind(canonical_id)`` callback (``None`` in production) the
    caller invokes once ``thread_start`` returns, to re-home the log onto the
    canonical id so it matches the ``{canonical}.jsonl`` request log and every
    other artifact keyed by the session's real id. No-op on resume (ids equal).

    PRIVATE SDK API — relies on ``codex._client._sync`` exposing ``_proc``,
    ``_stderr_lines`` and the ``_start_stderr_drain_thread`` slot; see the
    vendored-SDK update checklist (memory
    ``reference_codex_sdk_update_procedure``).
    """
    if not SDK_LOGGING_ENABLED:
        return None
    import threading

    sync_client = codex._client._sync
    # Lock-guarded so the post-thread_start rebind can re-home the log whether
    # or not the drain thread has opened the file yet. Only the one-shot open
    # and the rebind take the lock; the hot per-line write path stays lock-free
    # (the drain owns its ``sink`` locally, and on Linux the open fd follows the
    # inode across a rename). ``opened`` flips to True only *after* a successful
    # open, so a concurrent rebind never renames a not-yet-created file.
    lock = threading.Lock()
    state: dict[str, Any] = {"path": LOGS_DIR / f"{session_id}-stderr.log", "opened": False}

    def _start_tee_drain_thread() -> None:
        proc = sync_client._proc
        if proc is None or proc.stderr is None:
            return

        def _drain() -> None:
            stderr = proc.stderr
            if stderr is None:
                return
            with lock:
                path = state["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sink = open(path, "ab")
                except Exception:
                    logger.exception("Failed to open Codex stderr log %s", path)
                    sink = None
                state["opened"] = True
            try:
                for line in stderr:
                    sync_client._stderr_lines.append(line.rstrip("\n"))
                    if sink is not None:
                        try:
                            sink.write(line.encode() if isinstance(line, str) else line)
                            sink.flush()
                        except Exception:
                            logger.exception("Failed to write Codex stderr log %s", path)
                            sink.close()
                            sink = None
            finally:
                if sink is not None:
                    sink.close()

        sync_client._stderr_thread = threading.Thread(target=_drain, daemon=True)
        sync_client._stderr_thread.start()

    sync_client._start_stderr_drain_thread = _start_tee_drain_thread

    def _rebind(canonical_id: str) -> None:
        new_path = LOGS_DIR / f"{canonical_id}-stderr.log"
        with lock:
            old_path = state["path"]
            if new_path == old_path:
                return
            state["path"] = new_path
            if not state["opened"]:
                return  # drain hasn't opened yet; it will pick up new_path
            # Already open: the drain's fd follows the inode, so renaming the
            # file on disk transparently redirects its future writes.
            try:
                os.rename(old_path, new_path)
            except FileNotFoundError:
                pass  # open() failed earlier (sink is None); nothing to move
            except OSError:
                logger.exception("Failed to rename Codex stderr log %s -> %s", old_path, new_path)

    return _rebind
