# Peer Origin Routing — deferred follow-ups

**Status:** open list, nothing blocking
**Source:** the implementation of `docs/plans/2026-08-13-peer-origin-routing-plan.md`,
commit range `3584afc8..6a478a4c` on branch `peer-system`

Every item below was found during implementation, triaged, and deliberately deferred. None is
load-bearing: the feature ships correctly without them. Each carries the reason it was deferred,
so a later reader can re-judge it without re-deriving the context.

Two items the final reviewer triaged as **non-defects** are recorded at the end, so nobody
re-opens them.

---

## Locking and performance

These are all in the request hot path or its neighbourhood. None changes behaviour; each is a
cost or a latency note.

- **`synced_settings.read_routing_settings` double-acquires the `RLock`** on the request hot
  path. Correct — `RLock` is re-entrant — but one acquisition would do.
- **The atomic settings write holds `_settings_lock` while the gate acquires it synchronously in
  the event loop.** A latency note, not a regression in kind: the same shape existed before this
  work.
- **`_policy_cache` is a single-slot memo updated without a lock.** The rebuild is pure, so the
  worst case is a redundant rebuild, never a wrong policy.
- **`normalize_public_origin` is called twice per changed field** in the settings write path.
  Prescribed by the plan; the function is pure and cheap.
- **`test_general_and_routing_first_reads_share_one_source_observation` costs a fixed 250 ms.**
  Deterministic, not flaky — it needs a real window for the second reader to enter.

## Unreachable or redundant branches

- **`public_origin.py` — redundant `isascii()`** in the ASCII guard.
- **`origin_gate` — the "Not connected to the server" branch is near-unreachable**:
  `sendWsMessage` returns true while `wsSendFn` is set. Prescribed by the plan.

## Test coverage shape

- **The popover Apply/result wiring is covered by source regexes, not by executed code.** The
  behaviour is proven by the pure-helper tests; what is not executed is the wiring itself.
- **No generative one-way check that the JavaScript subset never rejects what Python accepts.**
  The property is verified today by a fixed case list and by 1,548 adversarial inputs tried during
  plan review, not by a generator. A generator would keep the property honest as both parsers
  evolve.

## Wording

- **`origin_gate.py` docstring still names the removed `ShareHostGate`.** The final reviewer
  judged this deliberate historical prose — see the non-defects section.
- **The event name `twicc:origin-settings-result` is narrower than the frame it carries.**
  Prescribed by the plan.
- **A Settings hint's copy** was flagged by the final review as improvable.
- **IPv6 hostname-hint bracketing** in a Settings hint.

## Lint

Both wait for the project-wide lint pass, which is deferred by an earlier decision. Fixing them
piecemeal would fight that decision.

- **`tests/test_origin_policy.py` — a plan-mandated `# noqa: E402` is unused** (RUF100 under
  expanded rule sets; the project's ruff config does not enable E402).
- **An unused `full` unpack in two new tests** (RUF059) — matches a pre-existing pattern.

## User-visible, small

- **An exact no-op Apply on a usable stored value clears the plain-HTTP Peer warning with no
  other feedback.** The user sees a warning disappear without having changed anything.

---

## Triaged as NON-defects — do not re-open

- **The `public_origin.py` empty-hostname branch IS reachable**, through the input `[]`. It was
  first reported as dead code; it is not.
- **The `origin_gate.py` docstring line naming `ShareHostGate`** is deliberate historical prose:
  it records what the gate replaced.
