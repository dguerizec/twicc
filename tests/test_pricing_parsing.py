"""Tests for the Claude model id parsers.

Two parsers handle Claude model identifiers:

- :meth:`ClaudeCodeHelpers.extract_family_and_version` parses
  OpenRouter ids (``anthropic/claude-...``) into ``(family, version)``.
- :func:`extract_model_info` parses Claude JSONL ``message.model``
  names into a :class:`ModelInfo`.

The two encodings are different (``4.5`` in OpenRouter vs ``4-5`` in
JSONL, optional date stamp on JSONL only, ``:thinking``/``-fast``
variants on OpenRouter only) but they describe the same models, so
they must agree on family and version. The :data:`MODEL_CASES` table
below is the single ground truth: each row pins down a model and
lists every encoding we expect each parser to handle. Both parsers
are exercised from that one table so a future divergence (e.g. the
shared :data:`CLAUDE_FAMILIES` set going out of sync with one
parser) is caught immediately.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

from twicc.core.enums import Provider
from twicc.providers.claude_code.pricing import CLAUDE_FAMILIES, extract_model_info
from twicc.providers.helpers import get_provider_helpers


@pytest.fixture(scope="module")
def helpers():
    return get_provider_helpers(Provider.CLAUDE_CODE)


# =============================================================================
# Ground truth: one row per model, with every observed encoding
# =============================================================================


class ModelCase(NamedTuple):
    """One Claude model and the encodings each parser must handle.

    ``openrouter_id`` is ``None`` when the model has no OpenRouter
    counterpart (e.g. JSONL-only artefacts of a transitional release).
    ``jsonl_names`` lists every JSONL ``message.model`` string we have
    actually observed for this model (with and without date stamp).
    JSONL strips the colon-suffix variant separator that OpenRouter
    uses, so ``anthropic/claude-3.7-sonnet:thinking`` shows up as
    ``claude-3-7-sonnet-thinking`` — same family, different separator.
    Add new observed names here as we see them; the cross-parser
    invariant test catches any drift between the two encodings.
    """
    family: str
    version: str
    openrouter_id: str | None
    jsonl_names: tuple[str, ...] = ()


MODEL_CASES: list[ModelCase] = [
    # New layout: family-then-version
    ModelCase("opus", "4.7", "anthropic/claude-opus-4.7", ("claude-opus-4-7",)),
    ModelCase("opus", "4.6", "anthropic/claude-opus-4.6", ("claude-opus-4-6",)),
    ModelCase("opus", "4.5", "anthropic/claude-opus-4.5",
              ("claude-opus-4-5", "claude-opus-4-5-20251101")),
    ModelCase("opus", "4.1", "anthropic/claude-opus-4.1", ("claude-opus-4-1",)),
    ModelCase("opus", "4", "anthropic/claude-opus-4", ("claude-opus-4",)),
    ModelCase("sonnet", "4.6", "anthropic/claude-sonnet-4.6", ("claude-sonnet-4-6",)),
    ModelCase("sonnet", "4.5", "anthropic/claude-sonnet-4.5",
              ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929")),
    ModelCase("sonnet", "4", "anthropic/claude-sonnet-4", ("claude-sonnet-4",)),
    ModelCase("haiku", "4.5", "anthropic/claude-haiku-4.5",
              ("claude-haiku-4-5",)),

    # Legacy layout: version-then-family
    ModelCase("sonnet", "3.7", "anthropic/claude-3.7-sonnet", ("claude-3-7-sonnet",)),
    ModelCase("sonnet", "3.5", "anthropic/claude-3.5-sonnet",
              ("claude-3-5-sonnet", "claude-3-5-sonnet-20241022")),
    ModelCase("haiku", "3.5", "anthropic/claude-3.5-haiku",
              ("claude-3-5-haiku", "claude-3-5-haiku-20241022")),
    ModelCase("haiku", "3", "anthropic/claude-3-haiku",
              ("claude-3-haiku", "claude-3-haiku-20240307")),

    # Variants. JSONL strips the colon and dash-joins the variant name.
    ModelCase("sonnet-thinking", "3.7", "anthropic/claude-3.7-sonnet:thinking",
              ("claude-3-7-sonnet-thinking",)),
    ModelCase("opus-fast", "4.6", "anthropic/claude-opus-4.6-fast",
              ("claude-opus-4-6-fast",)),
]


def _case_id(case: ModelCase) -> str:
    """Pretty-print a ``ModelCase`` as a parametrize id."""
    return f"{case.family}-{case.version}"


OPENROUTER_CASES = [c for c in MODEL_CASES if c.openrouter_id]
# Flatten (case, one jsonl name) pairs so each name shows up as its own
# parametrize id and a per-name failure points at the exact string.
JSONL_CASES = [
    (case, name)
    for case in MODEL_CASES
    for name in case.jsonl_names
]


# =============================================================================
# OpenRouter parser
# =============================================================================


@pytest.mark.parametrize("case", OPENROUTER_CASES, ids=_case_id)
def test_openrouter_id_decomposes_to_expected_family_and_version(helpers, case):
    assert helpers.extract_family_and_version(case.openrouter_id) == (
        case.family, case.version,
    )


@pytest.mark.parametrize(
    "model_id",
    [
        "openai/gpt-5.4",                # different OpenRouter provider
        "openai/gpt-5.4-mini",
        "anthropic/claude-quasar-7",     # unknown family
        "anthropic/claude-",              # bare prefix
        "",                               # empty
    ],
    ids=lambda v: v or "<empty>",
)
def test_openrouter_parser_rejects_unknown_input(helpers, model_id):
    assert helpers.extract_family_and_version(model_id) == (None, None)


def test_openrouter_parser_handles_family_only(helpers):
    # A bare family without a version still returns the family — version is None.
    assert helpers.extract_family_and_version("anthropic/claude-opus") == ("opus", None)


# =============================================================================
# JSONL parser
# =============================================================================


@pytest.mark.parametrize(
    ("case", "jsonl_name"),
    JSONL_CASES,
    ids=[f"{_case_id(c)}::{n}" for c, n in JSONL_CASES],
)
def test_jsonl_name_decomposes_to_expected_family_and_version(case, jsonl_name):
    info = extract_model_info(jsonl_name)
    assert info is not None
    assert (info.family, info.version) == (case.family, case.version)


def test_jsonl_parser_is_case_insensitive():
    # JSONL names should round-trip through any casing the SDK might emit.
    info = extract_model_info("Claude-Opus-4-7")
    assert info is not None
    assert (info.family, info.version) == ("opus", "4.7")


@pytest.mark.parametrize(
    "raw_name",
    [
        "gpt-5.4",                # different provider
        "opus-4-7",               # missing the "claude-" prefix
        "claude-quasar-7",        # unknown family
        "claude-opus",            # no version
        "",                       # empty
    ],
    ids=lambda v: v or "<empty>",
)
def test_jsonl_parser_rejects_unknown_input(raw_name):
    assert extract_model_info(raw_name) is None


# =============================================================================
# Cross-parser invariants
# =============================================================================


def test_both_parsers_use_the_same_family_set():
    # A drift in CLAUDE_FAMILIES would silently break one of the two
    # parsers — pin the bare family of every test case to that set.
    for case in MODEL_CASES:
        bare_family = case.family.split("-")[0]
        assert bare_family in CLAUDE_FAMILIES, (case, bare_family)


@pytest.mark.parametrize(
    "case",
    [c for c in MODEL_CASES if c.openrouter_id and c.jsonl_names],
    ids=_case_id,
)
def test_openrouter_and_jsonl_agree_on_family_and_version(helpers, case):
    """Both parsers must produce the same ``(family, version)`` for a
    model that appears in both encodings — including variants
    (``thinking``, ``fast``) which the JSONL encodes as a trailing
    dash-segment.

    Restricted to cases whose JSONL names we have actually observed:
    new combinations should be added to :data:`MODEL_CASES` as we see
    them, and any drift between the two parsers will surface here.
    """
    openrouter_result = helpers.extract_family_and_version(case.openrouter_id)
    for jsonl_name in case.jsonl_names:
        info = extract_model_info(jsonl_name)
        assert info is not None
        jsonl_result = (info.family, info.version)
        assert openrouter_result == jsonl_result, (
            f"Parsers disagree for {case.family} {case.version}: "
            f"OpenRouter {case.openrouter_id!r} -> {openrouter_result}, "
            f"JSONL {jsonl_name!r} -> {jsonl_result}"
        )
