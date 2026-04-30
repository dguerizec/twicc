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
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from twicc.core.enums import Provider

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
