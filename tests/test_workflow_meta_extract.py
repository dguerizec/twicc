"""Tests for :func:`extract_workflow_meta` — recovering the
``export const meta = {...}`` block from a saved Claude Code workflow ``.js``
file in pure Python (no Node, no deps).

The extractor is the risky, self-contained brick: parse a JS object literal
well enough to recover a saved workflow's ``name`` / ``description`` without
running JavaScript. These tests bolt it down with the two real saved
workflows plus a battery of adversarial inputs.

History: the extractor was first validated inline in this file, then ported
verbatim into ``twicc/providers/claude_code/workflow_meta.py``; the inline
copy was replaced by the import below and every assertion still passed —
proving the port is behaviour-identical.

``extract_workflow_meta`` is deliberately pure ``str -> dict``: it parses and
returns the whole ``meta`` object, and does NOT decide whether the result is
usable as a command (``name``/``description`` present, filename-vs-
``meta.name``, picker tag, …). Those integration decisions belong to the
discovery layer and are tested separately when that code lands.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

from twicc.providers.claude_code.workflow_meta import (
    WorkflowMetaError,
    extract_workflow_meta,
)


# ---------------------------------------------------------------------------
# Fixtures — the two real saved workflows (meta block + the trailing schema
# object that carries decoy name:/description: keys), embedded inline so the
# suite is hermetic (no dependency on ~/.claude which can vanish).
# ---------------------------------------------------------------------------

ROBOT_WORKFLOW = r"""export const meta = {
  name: 'robot-pipeline-sleep-test',
  description: 'Normal multi-phase workflow (4/3/2), 1M budget. Each agent bash-sleeps a random 5-15s AND generates text; data flows between phases.',
  phases: [
    { title: 'Assemble', detail: '4 agents: each invents a robot (+ bash sleep)' },
    { title: 'Slogan', detail: '3 agents: write an ad slogan for 3 robots (+ bash sleep)' },
    { title: 'Showdown', detail: '2 agents: one crowns the champion, one rates the fleet (+ bash sleep)' },
  ],
}

const CAP = 1000000
const ROBOT = {
  type: 'object', additionalProperties: false,
  required: ['name', 'trait', 'seconds'],
  properties: {
    name: { type: 'string', description: 'robot name' },
    trait: { type: 'string', description: 'its defining function/quirk, max 12 words' },
  },
}
"""

CONSTELLATION_WORKFLOW = r"""export const meta = {
  name: 'constellation-pipeline-sleep-test',
  description: 'Normal multi-phase workflow (4/3/2), 1M budget. Each agent bash-sleeps a random 5-15s AND generates text; data flows between phases.',
  phases: [
    { title: 'Chart', detail: '4 agents: each charts a star (+ bash sleep)' },
    { title: 'Myth', detail: '3 agents: weave a myth (+ bash sleep)' },
    { title: 'NorthStar', detail: '2 agents: crown the brightest (+ bash sleep)' },
  ],
}

const STAR = { name: 'WRONG', description: 'WRONG' }
"""


# ---------------------------------------------------------------------------
# Valid parses — (id, source, expected_name, expected_description)
# ---------------------------------------------------------------------------


class Valid(NamedTuple):
    id: str
    src: str
    name: str | None
    description: str | None


VALID_CASES: list[Valid] = [
    Valid(
        "real-robot",
        ROBOT_WORKFLOW,
        "robot-pipeline-sleep-test",
        "Normal multi-phase workflow (4/3/2), 1M budget. Each agent bash-sleeps a random 5-15s AND generates text; data flows between phases.",
    ),
    Valid(
        "real-constellation",
        CONSTELLATION_WORKFLOW,
        "constellation-pipeline-sleep-test",
        "Normal multi-phase workflow (4/3/2), 1M budget. Each agent bash-sleeps a random 5-15s AND generates text; data flows between phases.",
    ),
    Valid(
        "single-line",
        "export const meta = { name: 'oneliner', description: 'all on one line' }",
        "oneliner",
        "all on one line",
    ),
    Valid(
        "no-space-around-equals",
        "export const meta={name:'tight',description:'no spaces'}",
        "tight",
        "no spaces",
    ),
    Valid(
        "extra-spaces-around-equals",
        "export   const   meta   =   {\n  name:   'loose',\n  description:   'lots of space',\n}",
        "loose",
        "lots of space",
    ),
    Valid(
        "newline-before-brace",
        "export const meta =\n{\n  name: 'nl',\n  description: 'brace on next line',\n}",
        "nl",
        "brace on next line",
    ),
    Valid(
        "double-quoted-values",
        'export const meta = {\n  name: "double",\n  description: "quoted with doubles",\n}',
        "double",
        "quoted with doubles",
    ),
    Valid(
        "quoted-keys",
        "export const meta = {\n  'name': 'qkey',\n  \"description\": 'quoted keys too',\n}",
        "qkey",
        "quoted keys too",
    ),
    Valid(
        "escaped-apostrophe",
        r"export const meta = { name: 'apos', description: 'it\'s escaped, isn\'t it' }",
        "apos",
        "it's escaped, isn't it",
    ),
    Valid(
        "braces-and-commas-in-string",
        "export const meta = { name: 'braces', description: 'a report with { braces }, and, commas inside' }",
        "braces",
        "a report with { braces }, and, commas inside",
    ),
    Valid(
        "quotes-swapped-inside",
        "export const meta = { name: 'mixq', description: 'has \"double quotes\" inside singles' }",
        "mixq",
        'has "double quotes" inside singles',
    ),
    Valid(
        "backtick-value",
        "export const meta = {\n  name: `backtick`,\n  description: `templated-looking but literal`,\n}",
        "backtick",
        "templated-looking but literal",
    ),
    Valid(
        "line-comment-inside",
        "export const meta = {\n  name: 'lc', // this is the name\n  description: 'has a line comment above',\n}",
        "lc",
        "has a line comment above",
    ),
    Valid(
        "block-comment-inside",
        "export const meta = {\n  name: 'bc',\n  /* block comment */ description: 'after a block comment',\n}",
        "bc",
        "after a block comment",
    ),
    Valid(
        "trailing-comma",
        "export const meta = {\n  name: 'tc',\n  description: 'has a trailing comma',\n}",
        "tc",
        "has a trailing comma",
    ),
    Valid(
        "unicode-and-emdash",
        "export const meta = { name: 'uni', description: 'café — naïve résumé, 日本語 ok' }",
        "uni",
        "café — naïve résumé, 日本語 ok",
    ),
    Valid(
        "unicode-escape",
        r"export const meta = { name: 'esc', description: 'em—dash and newline\nhere' }",
        "esc",
        "em—dash and newline\nhere",
    ),
    Valid(
        "extra-fields-when-model",
        "export const meta = {\n  name: 'full',\n  description: 'has whenToUse and model',\n  whenToUse: 'when testing',\n  phases: [{ title: 'A', detail: 'x' }],\n  model: 'opus',\n}",
        "full",
        "has whenToUse and model",
    ),
    Valid(
        "leading-code-before-meta",
        "import { foo } from 'bar'\nconst name = 'not-the-workflow'\n\nexport const meta = {\n  name: 'afterimports',\n  description: 'meta comes after imports and a decoy const',\n}",
        "afterimports",
        "meta comes after imports and a decoy const",
    ),
    Valid(
        "commented-out-decoy-before",
        "// export const meta = { name: 'DECOY', description: 'DECOY' }\nexport const meta = {\n  name: 'real',\n  description: 'the real one, not the commented decoy',\n}",
        "real",
        "the real one, not the commented decoy",
    ),
]


@pytest.mark.parametrize("case", VALID_CASES, ids=[c.id for c in VALID_CASES])
def test_extract_name_and_description(case: Valid) -> None:
    meta = extract_workflow_meta(case.src)
    assert meta.get("name") == case.name
    assert meta.get("description") == case.description


def test_decoy_after_meta_not_captured() -> None:
    """The schema objects after `meta` carry name:/description: — they must
    never win over the real meta fields."""
    meta = extract_workflow_meta(ROBOT_WORKFLOW)
    assert meta["name"] == "robot-pipeline-sleep-test"
    assert meta["description"].startswith("Normal multi-phase")
    # not the decoy from the ROBOT schema's `name: { ..., description: 'robot name' }`
    assert meta["description"] != "robot name"

    meta2 = extract_workflow_meta(CONSTELLATION_WORKFLOW)
    assert meta2["name"] == "constellation-pipeline-sleep-test"
    assert meta2["description"] != "WRONG"


def test_phases_parsed_structurally() -> None:
    meta = extract_workflow_meta(ROBOT_WORKFLOW)
    phases = meta["phases"]
    assert [p["title"] for p in phases] == ["Assemble", "Slogan", "Showdown"]
    assert phases[0]["detail"] == "4 agents: each invents a robot (+ bash sleep)"


def test_all_top_level_keys_recovered() -> None:
    meta = extract_workflow_meta(VALID_CASES[-3].src)  # extra-fields-when-model
    assert set(meta) == {"name", "description", "whenToUse", "phases", "model"}
    assert meta["model"] == "opus"
    assert meta["whenToUse"] == "when testing"


def test_non_string_values_parsed() -> None:
    src = "export const meta = { name: 'nums', description: 'x', count: 42, ratio: 1.5, flag: true, nope: false, gone: null }"
    meta = extract_workflow_meta(src)
    assert meta["count"] == 42
    assert meta["ratio"] == 1.5
    assert meta["flag"] is True
    assert meta["nope"] is False
    assert meta["gone"] is None


def test_empty_meta_object_is_valid_but_has_no_name() -> None:
    """extract_workflow_meta parses structure; it does NOT enforce usability. An empty
    object parses fine and simply lacks name/description (the discovery layer
    decides what to do with that)."""
    meta = extract_workflow_meta("export const meta = {}")
    assert meta == {}
    assert "name" not in meta


def test_missing_name_still_returns_dict() -> None:
    meta = extract_workflow_meta("export const meta = { description: 'no name here' }")
    assert meta.get("name") is None
    assert meta["description"] == "no name here"


# ---------------------------------------------------------------------------
# Error cases — must raise WorkflowMetaError
# ---------------------------------------------------------------------------

ERROR_CASES: list[tuple[str, str]] = [
    ("no-meta-at-all", "const x = 1\nexport default x\n"),
    ("meta-is-number", "export const meta = 42"),
    ("meta-is-array", "export const meta = ['not', 'an', 'object']"),
    ("meta-is-string", "export const meta = 'nope'"),
    ("unterminated-object", "export const meta = { name: 'x', description: 'y'"),
    ("unterminated-string", "export const meta = { name: 'x', description: 'y }"),
    ("key-without-colon", "export const meta = { name 'x' }"),
    ("only-commented-meta", "// export const meta = { name: 'x', description: 'y' }\n"),
]


@pytest.mark.parametrize("src", [c[1] for c in ERROR_CASES], ids=[c[0] for c in ERROR_CASES])
def test_invalid_sources_raise(src: str) -> None:
    with pytest.raises(WorkflowMetaError):
        extract_workflow_meta(src)
