"""Codex-specific pricing primitives.

Two narrow helpers live here, both shaped by Codex's JSONL output:

- :func:`extract_model_info` parses the raw model name written to the
  Codex JSONL ``turn_context.payload.model`` field (e.g. ``"gpt-5-codex"``,
  ``"gpt-5.1-codex-max"``, ``"gpt-5.4"``) into ``(family, version)``.
  Used by :func:`twicc.providers.codex.helpers.serialize_model` and as
  the JSONL counterpart to
  :meth:`CodexHelpers.extract_family_and_version` (which parses
  OpenRouter ids); both must agree on ``(family, version)`` for the
  same model — exercised by ``tests/test_codex_pricing_parsing.py``.

- :func:`to_token_usage` flattens an OpenAI Responses-API
  ``last_token_usage`` dict (carried by Codex's
  ``event_msg.token_count`` events) into the cross-provider
  :class:`TokenUsage` that :func:`twicc.pricing.calculate_line_cost`
  consumes — splitting OpenAI's "input includes cache hits" total into
  the (fresh, cache_read) pair the cost calculator expects.

Everything else (OpenRouter fetch, ``ModelPrice`` sync, the actual cost
math, the periodic sync loop) lives in cross-provider modules
(:mod:`twicc.pricing`, :mod:`twicc.pricing_task`).
"""

from functools import lru_cache
from typing import NamedTuple

from twicc.pricing import TokenUsage


class ModelInfo(NamedTuple):
    """Family + version extracted from a Codex JSONL model name.

    ``family`` is the pricing-equivalence key (``"gpt"``, ``"gpt-codex"``,
    ``"gpt-codex-max"``, ``"gpt-mini"``, …). ``version`` is the dotted
    short form (``"5"``, ``"5.1"``, ``"5.4"``).
    """
    family: str
    version: str


@lru_cache(maxsize=32)
def extract_model_info(raw_name: str) -> ModelInfo | None:
    """Parse a raw Codex model name into ``(family, version)``.

    Codex writes its model identifier under ``turn_context.payload.model``
    as ``gpt-<version>[-<variant>...]`` where ``<version>`` is a
    dotted-numeric segment (``"5"``, ``"5.1"``, ``"5.4"``) and the
    optional variant suffix names a pricing-equivalence bucket folded
    into the family (``"codex"``, ``"codex-max"``, ``"mini"``, …).

    Examples:
        ``"gpt-5-codex"``        → ``ModelInfo("gpt-codex", "5")``
        ``"gpt-5.1-codex-max"``  → ``ModelInfo("gpt-codex-max", "5.1")``
        ``"gpt-5.4"``            → ``ModelInfo("gpt", "5.4")``
        ``"gpt-5.4-mini"``       → ``ModelInfo("gpt-mini", "5.4")``

    Date stamps (pure-numeric 3- to 4-digit segments — ``"0314"``,
    ``"0613"``, ``"1106"``) are dropped from the family suffix: they
    identify a specific snapshot rather than a separate pricing bucket.
    Anthropic's equivalent — the trailing ``YYYYMMDD`` stamp — is
    handled the same way by Claude's parser. Examples:

        ``"gpt-3.5-turbo-0613"`` → ``ModelInfo("gpt-turbo", "3.5")``
        ``"gpt-4-0314"``         → ``ModelInfo("gpt", "4")``
        ``"gpt-4-1106-preview"`` → ``ModelInfo("gpt-preview", "4")``

    Returns ``None`` for names that don't match the
    ``gpt-<version>...`` shape — bare prefix without a version
    (``"gpt-"``), non-numeric first segment (``"gpt-audio"``,
    ``"gpt-chat-latest"``, ``"gpt-oss-120b"``), version mixing digits
    with letters (``"gpt-4o"``, ``"gpt-4o-mini"``), and anything
    outside the OpenAI namespace (``"opus-4-7"``, ``"claude-opus-4"``,
    empty string).
    """
    if not raw_name.startswith("gpt-"):
        return None
    parts = raw_name.removeprefix("gpt-").split("-")
    first = parts[0]
    if not first or not all(ch.isdigit() or ch == "." for ch in first):
        return None
    extras = [
        part for part in parts[1:]
        if not (part.isdigit() and 3 <= len(part) <= 4)
    ]
    family = "gpt" if not extras else f"gpt-{'-'.join(extras)}"
    return ModelInfo(family=family, version=first)


def to_token_usage(last: dict) -> TokenUsage:
    """Convert a Codex JSONL ``last_token_usage`` dict to :class:`TokenUsage`.

    Codex ``event_msg.token_count`` events carry the OpenAI Responses-API
    counter shape under ``info.last_token_usage`` (and an identical
    cumulative shape under ``info.total_token_usage``):

    - ``input_tokens`` is the **total** prompt size for the call,
      cache hits included (unlike Anthropic's, where ``input_tokens``
      excludes cache reads);
    - ``cached_input_tokens`` is the cached subset of ``input_tokens``;
    - ``output_tokens`` is the total completion size, reasoning tokens
      included (OpenAI bills reasoning at the output rate — there's no
      separate pricing tier, so the per-line cost calculator doesn't
      need a dedicated counter for it);
    - ``reasoning_output_tokens`` and ``total_tokens`` are observability
      duplicates of subsets / sums of the above; ignored here.

    The cross-provider :class:`TokenUsage` expects ``input_tokens`` to
    be the **fresh** input only — :func:`twicc.pricing.calculate_line_cost`
    multiplies it by the full input price and then multiplies
    ``cache_read_input_tokens`` by the discounted cache-read price
    separately. To avoid double-billing we subtract the cached subset
    before storing it.

    Cache writes were free before GPT-5.6 (absorbed in the regular input
    price). **They are not free from GPT-5.6 on**: OpenAI bills prompt
    tokens written to cache at 1.25x the uncached input rate and reports
    them in a ``cache_write_tokens`` usage field.

    Codex does not pass that counter through. Its own ``TokenUsageBreakdown``
    schema declares only the five fields above, and no rollout carries it —
    checked on a real ``gpt-5.6-luna`` session. So both cache-write counters
    stay at zero here, not because the tier doesn't exist but because the
    number is absent at the source. Consequence: for GPT-5.6 models the
    per-line cost UNDERSTATES the true bill by up to 25% of its fresh-input
    component (pre-5.6 models are exact). Wire the counter in as soon as
    Codex emits it — the 1.25x prices are already in the synced OpenRouter
    rows and in :attr:`CodexHelpers.DEFAULT_FAMILY_PRICES`.
    """
    cached = last.get("cached_input_tokens", 0) or 0
    total_input = last.get("input_tokens", 0) or 0
    fresh_input = max(total_input - cached, 0)
    return TokenUsage(
        input_tokens=fresh_input,
        output_tokens=last.get("output_tokens", 0) or 0,
        cache_read_input_tokens=cached,
        cache_write_input_tokens_5m=0,
        cache_write_input_tokens_1h=0,
    )
