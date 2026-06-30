"""Validation of the ``rg -r<letter>`` PreToolUse guard *before* it is wired
into the Claude-Code agent.

Background: in ripgrep ``-r`` is ``--replace``, NOT ``--recursive`` (rg already
recurses by default). A glued short form like ``-rn`` / ``-rl`` / ``-rln`` is
therefore parsed as ``--replace=n`` and silently consumes the search as a
replacement template, corrupting the output with no error. The plan is a
``PreToolUse`` hook on the ``Bash`` tool that detects this exact mistake and
denies the call so the agent re-runs it without the ``r``.

This file validates the detection logic (regex + scoping helper) that lives in
``src/twicc/providers/claude_code/agent/agent.py`` and backs the ``PreToolUse``
Bash guard there.

Scope of the detection (decided in design discussion):
- ONLY the glued form ``-r`` immediately followed by a *letter*. A glued
  non-letter (``-r$1``, ``-r2``) is a deliberate replacement template, left
  alone. ``-r`` / ``--replace`` with a separate value is the legitimate replace.
- The ``r``-at-end-of-cluster twin (``-nr motif``) is intentionally NOT covered:
  never observed in practice and indistinguishable from an intended replace.
- Only tokens of an actual ``rg`` invocation are checked (the command word of a
  shell segment is ``rg``), so a ``-rn`` belonging to another command does not
  trigger.
"""

import pytest

from twicc.providers.claude_code.agent.agent import _RG_GLUED_REPLACE_RE, _rg_replace_trap

# --- 1. Token-level regex ----------------------------------------------------

# Tokens the regex MUST flag: a single dash, optional other short flags, then
# ``r`` glued to a letter (the replacement value).
GLUED_R_MATCH = [
    "-rn",     # the canonical mistake (-r with value "n")
    "-rl",
    "-rln",
    "-rN",     # uppercase letter still a glued value
    "-rna",    # extra chars after the matched pair
    "-nrl",    # other flag (n) before r, then glued l
    "-Srn",    # other flag (S) before r, then glued n
    "-inrx",   # several leading flags, r glued to x
]

# Tokens the regex must NOT flag.
GLUED_R_NOMATCH = [
    "-r",          # bare -r -> legitimate replace with a separate value
    "-n",          # unrelated short flag
    "-l",
    "-nr",         # r at END of cluster -> twin trap, deliberately ignored
    "-inr",        # r at end again
    "--replace",   # explicit long replace (intended) -> not a mistake
    "--replace=x",
    "--regexp",    # long option that merely contains "r"
    "-r2",         # glued digit -> deliberate replacement template
    "-r$1",        # glued non-letter (capture ref) -> deliberate template
    "foo-rn",      # does not start with a dash
    "rg",          # the command word itself
    "-",           # lone dash
    "",            # empty token
]


@pytest.mark.parametrize("token", GLUED_R_MATCH)
def test_regex_flags_glued_replace(token):
    assert _RG_GLUED_REPLACE_RE.match(token) is not None


@pytest.mark.parametrize("token", GLUED_R_NOMATCH)
def test_regex_ignores_safe_tokens(token):
    assert _RG_GLUED_REPLACE_RE.match(token) is None


# --- 2. Command-level helper: commands that MUST be blocked -------------------

BLOCK_COMMANDS = [
    "rg -rn foo",                       # textbook case
    "rg -rl foo",
    "rg -rln 'pattern' src/",
    "rg -nrl foo",                      # leading flag then glued r
    "rg -Srn foo",                      # smart-case flag then glued r
    "rg -rN foo",                       # glued uppercase value
    "rg -rn",                           # no pattern, still the trap
    "rg --color=never -rn foo",         # long flag before the glued one
    "/usr/bin/rg -rn foo",              # absolute path, basename is rg
    "cat x | rg -rn foo",               # rg in the second pipeline segment
    "rg -rn foo && echo done",          # rg in the first segment of a list
    "rg -rn foo ;",                     # trailing operator, last segment flushed
]


@pytest.mark.parametrize("command", BLOCK_COMMANDS)
def test_helper_blocks_rg_replace_trap(command):
    assert _rg_replace_trap(command) is True


# --- 3. Command-level helper: commands that must NOT be blocked ---------------

ALLOW_COMMANDS = [
    "rg foo",                           # plain search
    "rg -n foo",                        # unrelated flag
    "rg -l src/",
    "rg --replace bar foo",             # intended replace, long form
    "rg -r bar foo",                    # intended replace, value separate
    "rg -r '' foo",                     # intended replace with empty value
    "rg -nr foo",                       # r at end of cluster -> not covered
    "rg -r2 foo",                       # glued digit -> deliberate template
    "rg -r$1 foo",                      # glued capture ref -> deliberate template
    "rg --regexp=foo bar",             # long option containing "r"
    "grep -rn foo",                     # grep -r IS recursive: leave it alone
    "echo rg -rn",                      # rg/-rn are arguments of echo, not a call
    "cat rg-report.txt",                # rg only as a filename substring
    "othercmd -rn | rg foo",            # -rn belongs to othercmd, rg is clean
    "tar -rn archive ; rg foo",         # -rn on tar, separate rg segment
]


@pytest.mark.parametrize("command", ALLOW_COMMANDS)
def test_helper_allows_safe_commands(command):
    assert _rg_replace_trap(command) is False


# --- 4. Accepted heuristic limitations (documented, not bugs to fix) ----------
# These assert the CURRENT behaviour of a deliberately simple heuristic. All are
# fail-safe: a false positive only asks the agent to rephrase; a false negative
# just falls back to the status quo (no auto-correction). A real bash parser
# would close them, but that dependency is not worth it for a guardrail.


@pytest.mark.parametrize(
    "command",
    [
        "cat x|rg -rn foo",   # glued pipe -> shlex keeps "x|rg" as one token
        "sudo rg -rn foo",    # wrapper command -> segment[0] is "sudo", not "rg"
        "xargs rg -rn foo",
    ],
)
def test_known_false_negatives(command):
    # The trap is real but the segment's command word is not exactly ``rg``,
    # so it is missed. Documented, not blocked.
    assert _rg_replace_trap(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "rg -e '-rn' foo",    # -rn is the PATTERN value of -e, not a flag
    ],
)
def test_known_false_positives(command):
    # We do not parse which tokens are flag *values*, so a literal ``-rn`` passed
    # as a pattern is flagged. Rare; fail-safe (the agent is just asked to redo).
    assert _rg_replace_trap(command) is True
