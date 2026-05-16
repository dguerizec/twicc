"""Register the TwiCC marketplace and (re)install the TwiCC plugin in Codex.

Codex requires marketplaces and plugin enable-state to live in the user
config (``~/.codex/config.toml``): CLI overrides are explicitly ignored for
these two sections (see ``core-plugins/src/manager.rs:1979`` and
``installed_marketplaces.rs:21-23``). We therefore drive the official
JSON-RPC ``marketplace/add`` + ``plugin/install`` methods at TwiCC startup,
which persist the config and materialise the plugin into
``$CODEX_HOME/plugins/cache/twicc/twicc/<version>/``.

Both calls are idempotent:

- ``marketplace/add`` reports ``alreadyAdded`` when the marketplace is
  already registered (the persisted ``[marketplaces.twicc]`` section is
  left alone).
- ``plugin/install`` re-materialises the cache. For non-curated
  marketplaces the cache is keyed by the plugin's ``version`` field, so
  bumping ``plugin.json``'s version is what forces stale skill names /
  contents to refresh.

Failures are logged but never raised — TwiCC must keep starting even when
Codex is misconfigured or unreachable.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Manifest sub-paths Codex looks for when validating a marketplace root,
# matching ``core-plugins/src/marketplace.rs:20-23``.
MARKETPLACE_MANIFEST_CANDIDATES = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
)


async def ensure_twicc_plugin_installed() -> None:
    """Register the TwiCC marketplace and install the TwiCC plugin.

    Spawns a short-lived ``codex app-server`` client to drive the
    ``marketplace/add`` + ``plugin/install`` JSON-RPC dance, then closes.
    """
    from codex_app_server import AppServerConfig
    from codex_app_server.async_client import AsyncAppServerClient
    from codex_app_server.generated.v2_all import (
        MarketplaceAddResponse,
        PluginInstallResponse,
    )

    from twicc.agent.plugin import (
        MARKETPLACE_NAME,
        PLUGIN_NAME,
        get_marketplace_dir,
    )
    from twicc.providers.codex.bin import resolve_bundled_binary

    marketplace_dir = get_marketplace_dir().resolve()

    bundled_bin = resolve_bundled_binary()
    # ``cwd`` here is the app-server's own working directory — it has no
    # bearing on plugin install, which writes to ``$CODEX_HOME``.
    config = AppServerConfig(
        codex_bin=str(bundled_bin),
        cwd=str(Path.home()),
    )

    try:
        async with AsyncAppServerClient(config=config) as client:
            await client.initialize()

            # 1. Register the marketplace in ``~/.codex/config.toml``.
            add_resp = await client.request(
                "marketplace/add",
                {"source": str(marketplace_dir)},
                response_model=MarketplaceAddResponse,
            )
            if add_resp.already_added:
                logger.debug(
                    "TwiCC marketplace already registered (name=%s)",
                    add_resp.marketplace_name,
                )
            else:
                logger.info(
                    "Registered TwiCC marketplace (name=%s, root=%s)",
                    add_resp.marketplace_name,
                    add_resp.installed_root,
                )

            # 2. Find the marketplace manifest under ``installed_root`` —
            # ``plugin/install`` expects the path to the manifest file, not
            # the marketplace root.
            installed_root = Path(add_resp.installed_root.root)
            manifest = _find_marketplace_manifest(installed_root)
            if manifest is None:
                logger.error(
                    "TwiCC marketplace manifest not found under %s (looked for %s)",
                    installed_root,
                    ", ".join(MARKETPLACE_MANIFEST_CANDIDATES),
                )
                return

            # 3. Install the plugin: materialises the cache to the current
            # ``version`` in ``plugin.json`` and writes
            # ``[plugins."twicc@twicc"] enabled = true`` in the user config.
            await client.request(
                "plugin/install",
                {
                    "pluginName": PLUGIN_NAME,
                    "marketplacePath": str(manifest),
                },
                response_model=PluginInstallResponse,
            )
            logger.info(
                "Installed TwiCC plugin (plugin=%s, marketplace=%s)",
                PLUGIN_NAME,
                MARKETPLACE_NAME,
            )
    except Exception:
        logger.exception(
            "Failed to ensure TwiCC Codex plugin is installed — skills will be unavailable"
        )


def _find_marketplace_manifest(root: Path) -> Path | None:
    """Resolve the marketplace manifest path under ``root``, if any."""
    for candidate in MARKETPLACE_MANIFEST_CANDIDATES:
        path = root / candidate
        if path.is_file():
            return path
    return None
