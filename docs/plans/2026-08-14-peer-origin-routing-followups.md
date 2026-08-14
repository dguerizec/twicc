# Peer Origin Routing — deferred follow-ups

**Status:** open list, nothing blocking. Every contained item is closed (see **Resolved**) and
every performance item is decided against (see **Do NOT act**). What remains is two test-tooling
investments and one entry blocked on missing information.
**Source:** the implementation of `docs/plans/2026-08-13-peer-origin-routing-plan.md`,
commit range `3584afc8..6a478a4c` on branch `peer-system`

Every open item below was found during implementation, triaged, and deliberately deferred. None is
load-bearing: the feature ships correctly without them. Each carries the reason it was deferred,
so a later reader can re-judge it without re-deriving the context.

Items already closed move to the **Resolved** section, which keeps what each one actually was.
Items triaged as **non-defects** are recorded at the end, so nobody re-opens them.

---

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

- **The settings write holds `_settings_lock` while the gate acquires it synchronously in the
  event loop.** `_merge_and_write` runs in a worker thread (`sync_to_async`) and holds the lock for
  the WHOLE read-modify-write, not just the file replacement; meanwhile any incoming request makes
  the event-loop thread block on the same lock inside `read_routing_settings`, so every request
  stalls, settings-related or not. **Owner decision, 2026-08-14: leave it.** The window is the time
  to validate and rewrite a small JSON file, it only opens when a human clicks Apply, and the same
  shape predates this work. The fix — publishing an immutable snapshot that readers take without
  the lock — touches every reader of `_cache` and must preserve the re-entrance the
  read-modify-write callers depend on. Same trade as the entry below: invisible gain, wide blast
  radius. Re-open only if a real latency problem is ever observed.
- **`normalize_public_origin` is called twice per changed field** in the settings write path (once
  in the `_merge_and_write` loop, once inside `validate_origin_settings`). Prescribed by the plan.
  **Owner decision, 2026-08-14: leave it.** The path runs only when a human clicks Apply on an
  address, a handful of times in an instance's life, and the function is pure and cheap — the
  performance gain is nil. The only benefit would be clarity, and the refactor (having
  `validate_origin_settings` return its `results` dict, then feeding `corrections` /
  `normalized_patch` from it) has to preserve two easily-lost guarantees: the `null` rejection must
  stay BEFORE normalization, and a type-rejected field must stay out of `changed_fields` or the
  same error is reported twice. Higher risk of breaking something than of leaving it.
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
- **A dropped connection discarded an in-flight Apply in silence.** The `wsConnected` watcher now
  reports it per field before clearing, under the same retyped-field guard used everywhere else.
  The message is deliberately NOT the existing "Not connected to the server — try again.": a
  refused send never left the browser, while a drop may follow a write the server already applied.
  Consequence accepted: a stale callout survives on a write that did succeed. It clears on the next
  keystroke or Apply, like every other error in the component. Tracking the fields to clear on
  reconnect was judged not worth the extra state — a stale callout dissipates, silence never does.

---

## Triaged as NON-defects — do not re-open

- **The `public_origin.py` empty-hostname branch IS reachable**, through the input `[]`. It was
  first reported as dead code; it is not.
- **The `origin_gate.py` docstring line naming `ShareHostGate`** is deliberate historical prose:
  it records what the gate replaced.
- **The "Not connected to the server" branch is NOT under-covered.** It was reported as
  near-unreachable, so the disconnected case looked unhandled. Both halves are in fact handled,
  each with its own message: with the backend down at page load `wsSendFn` is `null`, so Apply
  returns false and the branch fires; a drop mid-flight is caught by the `wsConnected` watcher (see
  **Resolved**). Closed 2026-08-14. Do NOT "fix" it by threading VueUse's `send()` return value out
  of `sendWsMessage` — that touches every caller in the app for no gain.
