"""Claude Code-specific pricing primitives.

Two narrow helpers live here, both shaped by Claude Code's JSONL output:

- :func:`extract_model_info` parses the raw Claude model name written to
  the JSONL ``message.model`` field (e.g.
  ``"claude-opus-4-5-20251101"``) into ``(family, version)``. Used by
  :func:`twicc.providers.claude_code.helpers.serialize_model` for the
  bootstrap registry and by :mod:`compute` to build the OpenRouter
  ``model_id`` used as the pricing key.

- :func:`to_token_usage` flattens Anthropic's per-message ``usage``
  payload — including the ephemeral 5m / 1h cache breakdown nested
  under ``cache_creation`` — into the cross-provider :class:`TokenUsage`
  that :func:`twicc.pricing.calculate_line_cost` consumes.

Everything else (OpenRouter fetch, ``ModelPrice`` sync, the actual cost
math, the periodic sync loop) lives in cross-provider modules
(:mod:`twicc.pricing`, :mod:`twicc.pricing_task`).
"""

from functools import lru_cache
from typing import NamedTuple

from twicc.pricing import TokenUsage


# Known Claude family names. Hardcoded because Claude historically ships
# two segment orders ("claude-opus-4.7" vs "claude-3.5-sonnet"), so any
# parser of a Claude model id has to recognise the family name
# positionally rather than infer it from layout. Shared between the
# OpenRouter id parser on ``ClaudeCodeHelpers`` and the JSONL name
# parser :func:`extract_model_info` below.
CLAUDE_FAMILIES = frozenset({"fable", "opus", "sonnet", "haiku"})


class ModelInfo(NamedTuple):
    """Family + version extracted from a Claude JSONL model name.

    ``family`` is one of ``"opus"`` / ``"sonnet"`` / ``"haiku"``;
    ``version`` is the dotted short form (``"4.7"``, ``"4.5"``, …).
    """
    family: str
    version: str


@lru_cache(maxsize=32)
def extract_model_info(raw_name: str) -> ModelInfo | None:
    """Extract family and version from a raw Claude model name.

    Handles the various Claude naming patterns seen in JSONL files
    (with or without a dotted version, with or without a trailing date
    suffix).

    Examples:
        ``"claude-opus-4-5-20251101"`` → ``ModelInfo("opus", "4.5")``
        ``"claude-sonnet-4"``           → ``ModelInfo("sonnet", "4")``
        ``"claude-3-7-sonnet"``         → ``ModelInfo("sonnet", "3.7")``

    Variant markers trailing the version (``thinking``, ``fast``, …)
    are folded into the family the same way the OpenRouter parser
    does, so ``"claude-3-7-sonnet-thinking"`` and
    ``"claude-opus-4-6-fast"`` produce
    ``ModelInfo("sonnet-thinking", "3.7")`` and
    ``ModelInfo("opus-fast", "4.6")`` respectively. A trailing
    ``YYYYMMDD`` date stamp is recognised by length (≥ 6 digits) and
    dropped.

    Returns ``None`` when the format is not recognised.
    """
    if not raw_name.lower().startswith("claude-"):
        return None

    parts = raw_name.lower().removeprefix("claude-").split("-")

    family: str | None = None
    version_parts: list[str] = []
    extras: list[str] = []

    for part in parts:
        if part in CLAUDE_FAMILIES:
            family = part
            continue
        if part.isdigit():
            # ``YYYYMMDD`` date stamps (≥ 6 digits) follow the
            # family/version in the JSONL ``message.model`` string.
            # Treat them as opaque suffix and stop walking.
            if len(part) >= 6:
                break
            if len(version_parts) < 2:
                version_parts.append(part)
            continue
        # Non-digit, non-family segment.
        if family is not None and version_parts:
            # Variant trailing the version — fold into the family.
            extras.append(part)
        # else: pre-family/pre-version unrecognised token → ignore.

    if not family or not version_parts:
        return None

    if extras:
        family = f"{family}-{'-'.join(extras)}"

    version = ".".join(version_parts)
    return ModelInfo(family=family, version=version)


def to_token_usage(usage: dict) -> TokenUsage:
    """Convert a Claude JSONL ``message.usage`` dict to :class:`TokenUsage`.

    Anthropic exposes two cache-creation shapes:

    - the legacy ``cache_creation_input_tokens`` flat counter (5-minute
      bucket only — assigned to ``cache_write_input_tokens_5m``);
    - the modern ``cache_creation`` sub-object with
      ``ephemeral_5m_input_tokens`` and ``ephemeral_1h_input_tokens``
      that splits the count by TTL.

    The modern shape wins when present; otherwise we fall back to the
    flat field with the 1-hour bucket left at zero.
    """
    cache_creation = usage.get("cache_creation", {})
    if cache_creation:
        cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
        cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
    else:
        cache_5m = usage.get("cache_creation_input_tokens", 0)
        cache_1h = 0

    return TokenUsage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_input_tokens_5m=cache_5m,
        cache_write_input_tokens_1h=cache_1h,
    )
