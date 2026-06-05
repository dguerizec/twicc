"""Load the same data ``/api/bootstrap/`` returns, but in-process.

Used by the CLI to validate user inputs without making any HTTP call.
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent_settings_presets import read_agent_settings_presets
from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings


class ProviderBootstrap(NamedTuple):
    provider: Provider
    is_disabled: bool
    agent_settings_categories: dict
    agent_settings_choices: dict
    agent_settings_aliases: dict
    model_registry: list
    attachment_support: dict
    presets: list


class LocalBootstrap(NamedTuple):
    disabled_providers_present: bool
    disabled_providers: list[str]
    default_provider: str | None
    providers: dict[str, ProviderBootstrap]


def load_local_bootstrap() -> LocalBootstrap:
    """Build the bootstrap snapshot from on-disk + in-code sources."""
    synced = read_synced_settings()
    disabled_present = "disabledProviders" in synced
    disabled = synced.get("disabledProviders") or []
    default_provider = synced.get("defaultProvider") or None

    providers: dict[str, ProviderBootstrap] = {}
    for provider, helpers in get_provider_helpers_registry().items():
        provider_data = helpers.get_bootstrap_data() or {}
        presets = read_agent_settings_presets(provider).get("presets", [])
        providers[provider.value] = ProviderBootstrap(
            provider=provider,
            is_disabled=provider.value in disabled,
            agent_settings_categories=provider_data.get("agent_settings_categories", {}),
            agent_settings_choices=provider_data.get("agent_settings_choices", {}),
            agent_settings_aliases=provider_data.get("agent_settings_aliases", {}),
            model_registry=provider_data.get("model_registry", []),
            attachment_support=provider_data.get("attachment_support", {}),
            presets=presets,
        )

    return LocalBootstrap(
        disabled_providers_present=disabled_present,
        disabled_providers=disabled,
        default_provider=default_provider,
        providers=providers,
    )
