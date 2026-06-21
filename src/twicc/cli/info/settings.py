"""``twicc info settings`` — synced-settings schema (key → type, default, owner)."""

from __future__ import annotations

# Owner → short human-readable hint for CLI callers.
_OWNER_HINTS: dict[str, str] = {
    "generic": "settable via `twicc settings set <key> <value>`",
    "provider": "use `twicc settings provider <provider>`",
    "notifications": "use `twicc settings notifications`",
    "excluded": "UI-only, not settable via CLI",
    "unknown": "unrecognised key",
}


def build() -> dict:
    """Return a schema dict grouping every synced-settings key by owner.

    Each owner group is a list of entries, each carrying:

    - ``key``: the settings key name.
    - ``type``: Python type name of the default value (``"bool"``,
      ``"int"``, ``"str"``, ``"list"``, ``"dict"``).
    - ``default``: the current default value.
    - ``owner``: one of ``generic``, ``provider``, ``notifications``,
      ``excluded``.
    - ``hint``: a short message pointing callers to the right command.

    ``disabledProviders`` is included explicitly (it is intentionally
    absent from ``SYNCED_SETTINGS_DEFAULTS`` — its absence is a sentinel
    for the initial provider-activation dialog — but it is a valid
    provider-owned setting that callers need to discover).

    No JSON emission; no Django setup required (caller handles both).
    Caller must have already initialised Django.
    """
    from twicc.cli.settings._keys import classify_key
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    groups: dict[str, list[dict]] = {
        "generic": [],
        "provider": [],
        "notifications": [],
        "excluded": [],
    }

    for key, default in SYNCED_SETTINGS_DEFAULTS.items():
        owner = classify_key(key)
        entry = {
            "key": key,
            "type": type(default).__name__,
            "default": default,
            "owner": owner,
            "hint": _OWNER_HINTS.get(owner, ""),
        }
        # classify_key returns "unknown" only for keys not in
        # SYNCED_SETTINGS_DEFAULTS — that cannot happen here, but guard
        # gracefully by adding an "other" bucket if it ever fires.
        groups.setdefault(owner, []).append(entry)

    # ``disabledProviders`` is intentionally absent from
    # SYNCED_SETTINGS_DEFAULTS (its absence triggers the provider-activation
    # dialog on first launch). Include it explicitly so the schema is
    # complete: type list, no default sentinel (represented as null), owner
    # provider.
    groups["provider"].append({
        "key": "disabledProviders",
        "type": "list",
        "default": None,
        "owner": "provider",
        "hint": _OWNER_HINTS["provider"],
        "note": (
            "Absent from defaults by design — absence is the sentinel "
            "that triggers the initial provider-activation dialog."
        ),
    })

    return {
        "__description": (
            "Schema of every synced setting, grouped by owner. "
            "The 'generic' group lists keys settable via "
            "`twicc settings set`; other groups have dedicated commands."
        ),
        "groups": groups,
    }
