"""
Daily async task that detects retired model versions and auto-upgrades.

When a retirement is detected:
1. Global default is updated in synced settings (if affected)
2. Active processes are updated via the existing apply_live_settings machinery
3. A ``model_retirement`` broadcast notifies all frontends (retired model mapping)
   → Frontends handle display correction for non-running sessions on their own
   → No database mass-update of sessions (corrected at render/send time)
"""

import asyncio
import logging
from datetime import date as date_type

logger = logging.getLogger(__name__)

RETIREMENT_CHECK_INTERVAL = 24 * 60 * 60  # 24 hours

_retirement_stop_event: asyncio.Event | None = None


def get_retirement_stop_event() -> asyncio.Event:
    global _retirement_stop_event
    if _retirement_stop_event is None:
        _retirement_stop_event = asyncio.Event()
    return _retirement_stop_event


def stop_model_retirement_task() -> None:
    if _retirement_stop_event is not None:
        _retirement_stop_event.set()


async def start_model_retirement_task() -> None:
    """Run the retirement check loop: once at startup, then every 24 hours."""
    stop_event = get_retirement_stop_event()

    _log_upcoming_retirements()

    # Initial check on startup
    try:
        await _check_and_retire()
    except Exception:
        logger.exception("Error in initial retirement check")

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETIREMENT_CHECK_INTERVAL)
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # Time to check again

        try:
            await _check_and_retire()
        except Exception:
            logger.exception("Error in retirement check cycle")


async def _check_and_retire() -> None:
    """Perform one retirement check cycle."""
    from channels.layers import get_channel_layer

    from .model_registry import MODEL_VERSIONS, get_upgrade_target, is_model_retired
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    # Identify all retired non-latest versions
    retired_models: dict[str, str] = {}  # old selected_model → new selected_model
    for mv in MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        selected = f"{mv.model}-{mv.version}"
        if is_model_retired(selected):
            target = get_upgrade_target(selected)
            if target:
                retired_models[selected] = target

    if not retired_models:
        return

    logger.info("Retired models detected: %s", retired_models)

    # 1. Update global default if affected
    settings_changed = False
    from twicc.synced_settings import _settings_lock, prepare_settings_for_client, read_synced_settings, write_synced_settings

    with _settings_lock:
        current = read_synced_settings()
        default_model = current.get("defaultModel", SYNCED_SETTINGS_DEFAULTS["defaultModel"])
        if default_model in retired_models:
            current["defaultModel"] = retired_models[default_model]
            current["_version"] = current.get("_version", 0) + 1
            write_synced_settings(current)
            settings_changed = True
            logger.info("Updated global defaultModel: %s → %s", default_model, retired_models[default_model])

    # Broadcast global settings update if changed
    if settings_changed:
        channel_layer = get_channel_layer()
        clean, version = prepare_settings_for_client(read_synced_settings())
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "synced_settings_updated",
                    "settings": clean,
                    "version": version,
                },
            },
        )

    # 2. Update active processes (running sessions)
    # Model change is an "idle" setting: apply_live_settings() calls set_model()
    # on the SDK — no process restart needed.
    # - USER_TURN: applied immediately via set_model()
    # - ASSISTANT_TURN: apply_live_settings skips idle changes, so we also
    #   update the session DB row; _apply_pending_settings will pick it up
    #   at the next USER_TURN transition.
    from twicc.agent.manager import get_process_manager
    from .model_registry import (
        enforce_effort_max_consistency,
        enforce_effort_xhigh_consistency,
        selected_model_supports_1m,
    )

    manager = get_process_manager()
    # NOTE: ProcessManager doesn't expose a public get_all_processes() method.
    # We need to iterate over manager._processes.values() which gives ClaudeProcess instances.
    for process in list(manager._processes.values()):
        if process.selected_model not in retired_models:
            continue
        old_model = process.selected_model
        new_model = retired_models[old_model]
        ctx = process.context_max
        if ctx == 1_000_000 and not selected_model_supports_1m(new_model):
            ctx = 200_000
        # Update session DB so _apply_pending_settings picks it up if in ASSISTANT_TURN
        from twicc.core.models import Session
        session_updates: dict[str, object] = {"selected_model": new_model}
        adjusted_effort = enforce_effort_xhigh_consistency(
            new_model, enforce_effort_max_consistency(new_model, process.effort)
        )
        if adjusted_effort != process.effort:
            session_updates["effort"] = adjusted_effort
        await asyncio.to_thread(
            lambda sid=process.session_id, upd=session_updates: (
                Session.objects.filter(id=sid).update(**upd)
            )
        )
        try:
            await process.apply_live_settings(process.permission_mode, new_model, ctx)
            logger.info("Upgraded active process %s: %s → %s", process.session_id, old_model, new_model)
        except Exception:
            logger.exception("Failed to apply retirement upgrade to process %s", process.session_id)

    # 3. Broadcast model_retirement to frontends
    # Frontends use this to correct display/settings of non-running sessions
    # at render time (no DB update needed for those)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "model_retirement",
                "retired_models": retired_models,
                "default_changed": settings_changed,
            },
        },
    )


def _log_upcoming_retirements() -> None:
    """Log a summary of model versions and upcoming retirements at startup."""
    from .model_registry import MODEL_VERSIONS

    today = date_type.today()
    for mv in MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        days_left = (mv.retirement_date - today).days
        if days_left <= 0:
            logger.warning("Model %s-%s is RETIRED (since %s)", mv.model, mv.version, mv.retirement_date)
        elif days_left <= 30:
            logger.warning("Model %s-%s retires in %d days (%s)", mv.model, mv.version, days_left, mv.retirement_date)
        else:
            logger.info("Model %s-%s retires on %s (%d days)", mv.model, mv.version, mv.retirement_date, days_left)
