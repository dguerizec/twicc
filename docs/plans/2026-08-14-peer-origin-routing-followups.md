# Peer Origin Routing — deferred follow-ups

**Status:** open list, nothing blocking. The contained items are closed (see **Resolved**);
what remains needs a design decision, an arbitration, or tooling.
**Source:** the implementation of `docs/plans/2026-08-13-peer-origin-routing-plan.md`,
commit range `3584afc8..6a478a4c` on branch `peer-system`

Every open item below was found during implementation, triaged, and deliberately deferred. None is
load-bearing: the feature ships correctly without them. Each carries the reason it was deferred,
so a later reader can re-judge it without re-deriving the context.

Items already closed move to the **Resolved** section, which keeps what each one actually was.
Two items the final reviewer triaged as **non-defects** are recorded at the end, so nobody
re-opens them.

---

## Open — needs a decision before any code

Each one changes a behaviour or a structure. None is a mechanical edit.

- **The atomic settings write holds `_settings_lock` while the gate acquires it synchronously in
  the event loop.** A latency note, not a regression in kind: the same shape existed before this
  work. A fix means publishing an immutable settings snapshot that readers take without the lock,
  writers swapping the reference at the end. It touches every reader of `_cache` and must keep the
  re-entrance the read-modify-write callers rely on.
- **`normalize_public_origin` is called twice per changed field** in the settings write path
  (once in the `_merge_and_write` loop, once inside `validate_origin_settings`). Prescribed by the
  plan; the function is pure and cheap. A fix means `validate_origin_settings` returning its
  `results` dict and feeding `corrections`/`normalized_patch` from it. Two traps: the `null`
  rejection must stay BEFORE normalization, and a type-rejected field must stay out of
  `changed_fields` or the same error is reported twice.
- **`origin_gate` — the "Not connected to the server" branch is near-unreachable**:
  `sendWsMessage` returns true while `wsSendFn` is set, and `wsSendFn` is only cleared on the
  `CLOSED` transition. Prescribed by the plan. The neighbouring real gap: the `wsConnected` watcher
  clears pending writes with no message at all, so a write sent just before a drop fails silently.
  Fix that watcher, not `sendWsMessage` — threading VueUse's `send()` return value out would touch
  every caller in the app.

## Open — needs tooling

- **The popover Apply/result wiring is covered by source regexes, not by executed code.** The
  behaviour is proven by the pure-helper tests; what is not executed is the wiring itself. `npm
  test` is a bare `node --test`: no `@vue/test-utils`, no jsdom, no vitest, so no SFC can be
  mounted. Either add the component-test tooling, or extract the wiring (`applyOriginSetting`,
  `onOriginSettingsResult`, the `wsConnected` watcher) into an injectable pure module. The second
  needs no new dependency and also makes the two entries above testable.
- **No generative one-way check that the JavaScript subset never rejects what Python accepts.**
  The property is verified today by a fixed case list and by 1,548 adversarial inputs tried during
  plan review, not by a generator. A generator would keep the property honest as both parsers
  evolve. It needs both parsers over the same random inputs, so a cross-language runner and `node`
  in the Python test environment — no such precedent exists in `tests/`.

## Open — blocked on missing information

- **A Settings hint's copy** was flagged by the final review as improvable. The record does not
  say which hint, nor what the objection was. Unactionable until someone recovers both. If nobody
  can, close it.

## Do NOT act on these

- **The two lint findings** — `tests/test_origin_policy.py`'s unused plan-mandated `# noqa: E402`
  (RUF100, only under expanded rule sets: the project config does not enable E402) and three
  unused `full` unpacks in `tests/test_share_host_gate.py` (RUF059, matching a pre-existing
  pattern). They wait for the project-wide lint pass, deferred by an earlier decision. Fixing them
  piecemeal fights that decision.
- **`_policy_cache` is a single-slot memo updated without a lock.** Leave it. The tuple assignment
  is atomic, the rebuild is pure, so the worst case is a redundant rebuild, never a wrong policy.
  A snapshot-publishing fix for the lock entry above would absorb it anyway.
- **Do not back-propagate the `twicc:synced-settings-result` rename** into
  `2026-08-13-peer-origin-routing-plan.md`. That plan is a historical record and keeps the name it
  prescribed.

---

## Resolved

Closed on 2026-08-14, after a review of the whole list. Each was contained, fully covered by an
existing or added test, and carried no behavioural risk.

- **`synced_settings.read_routing_settings` double-acquired the `RLock`.** Both public readers now
  go through `_read_synced_settings_locked()`, which the caller enters holding the lock.
- **`public_origin.py` — redundant `isascii()`** removed: the `0x21..0x7e` range check already
  implies ASCII. The neighbouring `"%"` check does not, and stays.
- **`test_general_and_routing_first_reads_share_one_source_observation`** now waits 50 ms instead
  of 250 ms. The window only bounds regression-detection power; the passing path always consumes
  it whole.
- **The event name `twicc:origin-settings-result`** became `twicc:synced-settings-result`: the
  frame carries the result of ANY correlated synced-settings write, not only an origin. The
  implementation plan keeps the old name — it is a historical record.
- **IPv6 hostname bracketing.** `own_display_name()` returned the bare canonical hostname, so an
  IPv6 peer address advertised itself as `::1` instead of `[::1]`. It was not only a hint: the
  value goes out in every handshake.
- **An exact no-op Apply cleared the plain-HTTP Peer warning with no other feedback.** The warning
  now precedes the empty-patch early return, so it describes the value being applied.

---

## Triaged as NON-defects — do not re-open

- **The `public_origin.py` empty-hostname branch IS reachable**, through the input `[]`. It was
  first reported as dead code; it is not.
- **The `origin_gate.py` docstring line naming `ShareHostGate`** is deliberate historical prose:
  it records what the gate replaced.
