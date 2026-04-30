"""
Per-provider helpers and their cross-provider registry.

Defines the abstract surface that every backend provider implements
(``BaseProviderHelpers``) and the singleton registry that exposes one
helpers instance per provider (``get_provider_helpers``). Mirrors the
role of ``BaseAgentManager`` (process management) and the WebSocket
handlers: when the core needs to do something whose details depend on
which provider produced a session — parse the session content,
serialize a model identifier, persist a new title, contribute fields to
the bootstrap payload — it calls into ``BaseProviderHelpers`` and the
registry routes the call to the right implementation.

Concrete providers live in ``providers/<name>/helpers.py`` and subclass
``BaseProviderHelpers``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from twicc.core.enums import Provider


class AgentSettingCategory(StrEnum):
    """When a per-agent setting can be applied to a running process.

    Each provider classifies its own keys per category in
    :attr:`BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES`.

    - ``LIVE``: applicable at any time (USER_TURN or ASSISTANT_TURN)
    - ``IDLE``: applicable only during USER_TURN
    - ``STARTUP``: applicable only at process creation (requires restart)
    """
    LIVE = "live"
    IDLE = "idle"
    STARTUP = "startup"

if TYPE_CHECKING:
    from twicc.core.models import SessionItem


class TitleValidationResult(NamedTuple):
    """Outcome of validating a user-supplied session title.

    ``title`` is the normalized value when valid, ``None`` otherwise.
    ``error`` is a human-readable message when invalid, ``None`` otherwise.
    """
    title: str | None
    error: str | None


class UserMessage(NamedTuple):
    """One indexable user message in a session, produced by ``get_user_messages``."""
    line_num: int
    timestamp: datetime | None
    text: str


class IndexableMessage(NamedTuple):
    """One indexable message (user or assistant) for full-text search indexing.

    Produced by ``get_indexable_messages``; consumed by the search reindex path.
    """
    line_num: int
    text: str
    from_role: str  # "user" or "assistant"
    timestamp: datetime | None


class BaseProviderHelpers:
    """Abstract per-provider helpers."""

    # Provider-specific entries to merge into ``SYNCED_SETTINGS_DEFAULTS``.
    # Keys must be namespaced (e.g. ``claudeCodeDefault*``) to avoid clashes
    # between providers and with the cross-provider generic defaults.
    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {}

    # Provider-specific legacy → current key renames applied at read time so
    # old ``settings.json`` files keep their values across renames.
    RENAMED_SYNCED_SETTINGS_KEYS: ClassVar[dict[str, str]] = {}

    # Provider-specific legacy keys to drop unconditionally on read (no longer
    # used). Aggregated with each other provider's contribution and with the
    # cross-provider generic list during the settings migration.
    OBSOLETE_SYNCED_SETTINGS_KEYS: ClassVar[tuple[str, ...]] = ()

    # Per-agent settings classified by when they can be applied to a running
    # process. Each provider defines its own keys per :class:`AgentSettingCategory`.
    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = {}

    # Per-agent setting field name → corresponding key in the synced settings
    # used as fallback when the session-level value is unset. Drives
    # :meth:`resolve_agent_settings`. Each provider defines its own mapping.
    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = {}

    def resolve_agent_settings(self, source: dict | Any) -> dict:
        """Return the effective per-agent settings, with synced defaults as fallback.

        ``source`` is either a dict of override values (e.g. fields parsed
        from a WebSocket message) or any object exposing the per-agent
        setting field names as attributes (typically a ``Session`` row). For
        each field listed in :attr:`AGENT_SETTINGS_FIELDS_MAPPING`, the
        explicit value wins if non-``None``; otherwise the corresponding
        synced settings default is used, with the helper's own
        :attr:`SYNCED_SETTINGS_DEFAULTS` as a last-resort fallback.
        """
        from twicc.synced_settings import read_synced_settings

        synced = read_synced_settings()
        is_dict = isinstance(source, dict)
        result: dict = {}
        for field, default_key in self.AGENT_SETTINGS_FIELDS_MAPPING.items():
            value = source.get(field) if is_dict else getattr(source, field, None)
            if value is None:
                value = synced.get(default_key, self.SYNCED_SETTINGS_DEFAULTS.get(default_key))
            result[field] = value
        return result

    def classify_agent_settings_changes(
        self,
        current: dict,
        requested: dict,
    ) -> dict[AgentSettingCategory, list[str]]:
        """Return per-category lists of keys that differ between ``current`` and ``requested``.

        Default implementation derives the per-category diff from
        :attr:`AGENT_SETTINGS_CATEGORIES`. Categories with no changes return an
        empty list.
        """
        result: dict[AgentSettingCategory, list[str]] = {
            category: [] for category in self.AGENT_SETTINGS_CATEGORIES
        }
        for category, keys in self.AGENT_SETTINGS_CATEGORIES.items():
            for key in keys:
                if current.get(key) != requested.get(key):
                    result[category].append(key)
        return result

    def get_user_messages(self, items: Iterable[SessionItem]) -> list[UserMessage]:
        """Extract user messages with text from ``items``.

        ``items`` are session items already filtered to user messages and
        ordered by line number; the caller owns that fetch (it's a generic
        DB lookup), the helper owns the ``content`` parsing.
        """
        raise NotImplementedError

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        """Extract indexable messages from ``items``.

        ``items`` are session items already filtered to user/assistant
        messages and ordered by line number.
        """
        raise NotImplementedError

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        """Return the tool_result payload entries for ``tool_use_id`` across ``items``.

        ``items`` are the session items already filtered to those known to
        carry the relevant tool_result lines (typically resolved by the
        caller via :class:`ToolResultLink`).
        """
        raise NotImplementedError

    def serialize_model(self, model: str | None) -> dict | None:
        """Serialize a raw model identifier into ``{raw, family, version}``.

        Returns ``None`` for an empty input.
        """
        raise NotImplementedError

    def validate_title(self, title: str | None) -> TitleValidationResult:
        """Validate and normalize a user-supplied session title.

        Each provider may apply its own length / format rules.
        """
        raise NotImplementedError

    def rename_session(self, session_id: str, title: str) -> None:
        """Persist a new title in the provider's session storage.

        Only writes to the provider-specific backing store (e.g. JSONL for
        Claude Code). The DB row and the cross-provider title-protection
        machinery are handled by the caller.
        """
        raise NotImplementedError

    def get_bootstrap_data(self) -> dict:
        """Return provider-specific keys merged into the ``/api/bootstrap/`` payload.

        Default implementation contributes nothing.
        """
        return {}


class ProviderHelpersRegistry:
    """Singleton holding one :class:`BaseProviderHelpers` per provider.

    Mirrors :class:`twicc.agent.registry.AgentManagerRegistry` and
    ``twicc.asgi.WSConsumer.PROVIDER_HANDLERS``: providers are declared
    statically as a class attribute and instantiated once when the
    singleton is created.
    """

    PROVIDER_HELPERS: ClassVar[dict[Provider, type[BaseProviderHelpers]]]

    def __init__(self) -> None:
        # Imported here to avoid a circular import at module load time:
        # each provider helpers module imports from this one.
        from twicc.providers.claude_code.helpers import ClaudeCodeHelpers

        self.PROVIDER_HELPERS = {
            Provider.CLAUDE_CODE: ClaudeCodeHelpers,
        }
        self._helpers: dict[Provider, BaseProviderHelpers] = {
            key: cls() for key, cls in self.PROVIDER_HELPERS.items()
        }

    def get(self, provider: Provider) -> BaseProviderHelpers:
        """Return the helpers instance for ``provider``."""
        return self._helpers[provider]

    def items(self) -> list[tuple[Provider, BaseProviderHelpers]]:
        """Return ``(provider, helpers)`` pairs for every registered provider."""
        return list(self._helpers.items())

    def values(self) -> list[BaseProviderHelpers]:
        """Return the helpers instances for every registered provider."""
        return list(self._helpers.values())


_registry: ProviderHelpersRegistry | None = None


def get_provider_helpers_registry() -> ProviderHelpersRegistry:
    """Return the global :class:`ProviderHelpersRegistry` (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = ProviderHelpersRegistry()
    return _registry


def get_provider_helpers(provider: Provider | str) -> BaseProviderHelpers:
    """Return the :class:`BaseProviderHelpers` for ``provider``.

    Accepts either a :class:`Provider` enum value or its string form (the
    ``Session.provider`` field is stored as a string).
    """
    if isinstance(provider, str):
        provider = Provider(provider)
    return get_provider_helpers_registry().get(provider)
