# Context offload

When your own context is filling up, hand the rest of the job to a fresh child with
a compact briefing, instead of grinding to a halt.

Shape: chain · files + push/pull · staged · chain · —.

## Who does what
- **Any orchestrating session (leader or manager)** — when your context usage climbs
  (it's reported in your context block each turn), stop accreting: write the state
  that matters to the shared scratch, spawn a fresh child with a tight briefing plus
  a pointer to that state, and let it carry on. Aggregate its result as usual.
- **The fresh child** — picks up from the briefing and scratch state, with none of
  your history; finishes the job and reports back.

## Protocol
1. Watch your context usage (reported each turn).
2. Before it's critical, dump the essential state to a scratch file — decisions so
   far, what's left, pointers — not your whole transcript.
3. Spawn a fresh executor briefed with the remaining goal + the scratch file path.
4. Collect its result; it becomes your deliverable.

## Use it when
A job is longer than one context can hold, or you've done the heavy thinking and the
remainder is mechanical.
Not a substitute for splitting work you can already see is large (→ divide-and-conquer).

## Pitfalls
- The briefing must be self-contained — the child has none of your memory.
- Offload *state*, not your transcript: a fresh child wants the conclusions, not the journey.
- Read-only sessions can't write the scratch — this is an executor move.

Examples: `examples/context-relay.md`.
