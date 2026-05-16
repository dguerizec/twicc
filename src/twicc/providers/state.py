"""Runtime state machine for each provider's orchestrator.

While ``twicc.providers.enabled`` answers the *intent* question — does the
user want this provider on or off — this module answers the *runtime*
question: where is the provider actually in its lifecycle right now?

Four states cover the full cycle:

- ``stopped``  — orchestrator has never started, or its ``shutdown()`` is done.
- ``starting`` — ``orchestrator.start()`` is in progress (initial sync, plugin
                 install, watcher boot, ...). Runtime calls are refused.
- ``running``  — ``start()`` returned successfully; the provider is operational.
- ``stopping`` — ``shutdown()`` is in progress. Runtime calls are refused.

Why a separate state machine on top of the enabled list:

- ``start()`` and ``shutdown()`` can take seconds; the user can click the
  Settings switch off and back on faster. Without a transient state, the
  back can end up in inconsistent intermediate configurations.
- ``ensure_provider_running`` is the strict gate used by every runtime
  endpoint (send message, rename, pending request response, ...). A
  provider that is enabled but still in ``starting`` is NOT yet ready;
  refusing the call is the correct behaviour.

Every transition broadcasts a ``provider_state_changed`` WS message so
all connected clients can sync their UI (e.g. grey out the Settings
switch with a spinner during transitions).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.enums import Provider
from twicc.providers.enabled import ProviderDisabledError

logger = logging.getLogger(__name__)


class ProviderState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


# Module-level dict. Every Provider enum value is implicitly STOPPED until
# the orchestrator transitions it (kept sparse on purpose — the snapshot
# helper materialises the default).
_states: dict[Provider, ProviderState] = {}


def get_provider_state(provider: Provider) -> ProviderState:
    return _states.get(provider, ProviderState.STOPPED)


def get_all_provider_states() -> dict[str, str]:
    """Snapshot of every registered provider's state.

    Always returns every ``Provider`` enum value (with ``stopped`` as the
    default for entries not yet in ``_states``), so the frontend can rely
    on every provider being present in the dict.
    """
    return {p.value: get_provider_state(p).value for p in Provider}


def is_provider_running(provider: Provider) -> bool:
    return get_provider_state(provider) == ProviderState.RUNNING


def ensure_provider_running(provider: Provider) -> None:
    """Raise ``ProviderDisabledError`` if ``provider`` is not currently running.

    Stricter than :func:`twicc.providers.enabled.ensure_provider_enabled`:
    a provider in ``starting`` or ``stopping`` is refused too, because
    its task graph is not in a steady state and a runtime call could race
    against the lifecycle transition. The frontend already greys out the
    relevant UI during transitions, so this raises in practice only on
    races.

    Reusing ``ProviderDisabledError`` keeps the frontend's existing toast
    handler (``code: "provider_disabled"``) working without changes —
    from the user's perspective, "not yet running" and "disabled" surface
    the same way (try again in a second / re-enable in Settings).
    """
    if not is_provider_running(provider):
        raise ProviderDisabledError(provider)


async def set_provider_state(provider: Provider, state: ProviderState) -> None:
    """Update the in-memory state and broadcast the change to every client.

    No-op if the new state matches the current one (avoids spamming the
    WS with duplicate transitions). Logs every real change at INFO level
    so the lifecycle is auditable from ``backend.log``.
    """
    previous = _states.get(provider, ProviderState.STOPPED)
    if previous == state:
        return
    _states[provider] = state
    logger.info("Provider state: %s -> %s", previous.value, state.value)
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "provider_state_changed",
                "provider": provider.value,
                "state": state.value,
            },
        },
    )


async def force_disable_after_failed_start(provider: Provider) -> None:
    """Persist ``provider`` into ``disabledProviders`` after a failed start.

    Called by the orchestrator when ``orch.start()`` raises during a hot
    activation. The state has already been rolled back to ``stopped`` by
    the caller; this helper adds the provider to ``disabledProviders``
    on disk so a server restart doesn't blindly retry the failed start,
    and broadcasts the new synced-settings snapshot so every client's
    Settings switch flips back to off.

    Idempotent — if the provider is already in ``disabledProviders`` no
    work is done (this happens on startup if the user had previously
    explicitly disabled it; the state machine still records the failed
    start, but the persisted config doesn't need to change).
    """
    from twicc.synced_settings import (
        _settings_lock,
        read_synced_settings,
        write_synced_settings,
    )

    def _persist() -> tuple[list[str], int] | None:
        with _settings_lock:
            settings = read_synced_settings()
            disabled = set(settings.get("disabledProviders") or [])
            if provider.value in disabled:
                return None
            disabled.add(provider.value)
            new_disabled_list = sorted(disabled)
            settings["disabledProviders"] = new_disabled_list
            new_version = settings.get("_version", 0) + 1
            settings["_version"] = new_version
            write_synced_settings(settings)
            return new_disabled_list, new_version

    result = await sync_to_async(_persist)()
    if result is None:
        return
    new_disabled_list, new_version = result

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "synced_settings_updated",
                "settings": {"disabledProviders": new_disabled_list},
                "version": new_version,
            },
        },
    )
