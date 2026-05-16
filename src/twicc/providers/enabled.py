"""Runtime source of truth for which providers are currently enabled.

The state is derived from the `disabledProviders` key in the synced settings.
A provider is *enabled* if it is registered (compiled in) AND not in the
`disabledProviders` list.

If the `disabledProviders` key is **absent** from the synced settings, this
module returns "no provider is enabled" so the rest of the backend stays
idle until the user makes a choice via the initial dialog (cf. spec §2/§3).

Callers that perform runtime actions on a specific provider MUST call
`ensure_provider_enabled(provider)` first. Read-only paths (DB queries,
parsing of historical session content) do NOT need the gate — see spec §6.3.
"""

from __future__ import annotations

from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings


class ProviderDisabledError(Exception):
    """Raised when an operation targets a provider that is currently disabled."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        super().__init__(f"Provider {provider.value} is disabled")


def _has_disabled_providers_key() -> bool:
    """Return True if the `disabledProviders` key is physically present in
    the synced settings file. The mere absence of the key is the sentinel
    used to trigger the initial dialog (cf. spec §2.1)."""
    return "disabledProviders" in read_synced_settings()


def get_disabled_providers() -> set[Provider]:
    """Return the set of providers explicitly disabled by the user.

    Returns an empty set if the key is absent. Unknown provider names are
    silently dropped (forward-compat with futures that ship a renamed
    provider — never raises on stale strings)."""
    raw = read_synced_settings().get("disabledProviders") or []
    if not isinstance(raw, list):
        return set()
    valid = {p.value for p in Provider}
    return {Provider(v) for v in raw if v in valid}


def get_enabled_providers() -> set[Provider]:
    """Return the set of providers that are both registered AND enabled.

    If `disabledProviders` is absent from settings (= no choice made yet),
    returns an empty set — the back stays idle until the user validates the
    initial dialog."""
    if not _has_disabled_providers_key():
        return set()
    registered = {p for p, _ in get_provider_helpers_registry().items()}
    disabled = get_disabled_providers()
    return registered - disabled


def is_provider_enabled(provider: Provider) -> bool:
    return provider in get_enabled_providers()


def ensure_provider_enabled(provider: Provider) -> None:
    """Raise `ProviderDisabledError` if `provider` is not currently enabled."""
    if not is_provider_enabled(provider):
        raise ProviderDisabledError(provider)
