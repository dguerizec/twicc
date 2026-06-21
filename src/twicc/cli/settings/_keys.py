"""Pure helpers for the generic `twicc settings set/unset/get` backbone."""
from __future__ import annotations

# Keys excluded from CLI mutation (visual-only or internal).
EXCLUDED_KEYS = frozenset({"waTheme", "waBrand", "defaultLayoutId", "_version"})
# Keys owned by dedicated sub-commands.
PROVIDER_KEYS = frozenset({"defaultProvider", "disabledProviders",
                           "orchestrationDisabledProviders"})
NOTIFICATION_KEYS = frozenset({"externalNotificationTargets"})


class ValueParseError(ValueError):
    pass


def _provider_prefixed(key: str) -> bool:
    return key.startswith("claudeCode") or key.startswith("codex")


def classify_key(key: str) -> str:
    """One of: generic | provider | notifications | excluded | unknown."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    if key in EXCLUDED_KEYS:
        return "excluded"
    if key in PROVIDER_KEYS or _provider_prefixed(key):
        return "provider"
    if key in NOTIFICATION_KEYS:
        return "notifications"
    if key in SYNCED_SETTINGS_DEFAULTS:
        return "generic"
    return "unknown"


_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def parse_value(key: str, raw: str):
    """Parse `raw` into the type of the key's default."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    default = SYNCED_SETTINGS_DEFAULTS[key]
    if isinstance(default, bool):
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueParseError(f"{key} expects a boolean (true/false), got {raw!r}.")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            raise ValueParseError(f"{key} expects an integer, got {raw!r}.")
    return raw
