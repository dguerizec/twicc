"""Codex-specific pricing primitives.

A single narrow helper lives here:

- :func:`extract_model_info` parses the raw model name written to the
  Codex JSONL ``turn_context.payload.model`` field (e.g. ``"gpt-5-codex"``,
  ``"gpt-5.1-codex-max"``, ``"gpt-5.4"``) into ``(family, version)``.
  Used by :func:`twicc.providers.codex.helpers.serialize_model` and as
  the JSONL counterpart to
  :meth:`CodexHelpers.extract_family_and_version` (which parses
  OpenRouter ids); both must agree on ``(family, version)`` for the
  same model — exercised by ``tests/test_codex_pricing_parsing.py``.

Everything else (OpenRouter fetch, ``ModelPrice`` sync, the actual cost
math, the periodic sync loop) lives in cross-provider modules
(:mod:`twicc.pricing`, :mod:`twicc.pricing_task`).
"""

from functools import lru_cache
from typing import NamedTuple


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
