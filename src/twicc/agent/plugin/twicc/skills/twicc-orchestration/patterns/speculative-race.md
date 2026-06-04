# Speculative race (first-wins)

Run several attempts at the same task in parallel; take the first acceptable result
and stop the rest.

Shape: star · pull + push · first-wins · select · heterogeneous (varied approaches).

## Who does what
- **Leader or manager** — spawn K children on the same goal, each with a different
  approach (model, preset, or strategy). Take the first that succeeds, `stop` the
  others, use its result.
- **Workers** — each attempts the whole task independently; report success with the
  result, or failure.

## Protocol
1. Spawn K attempts, ideally diverse (`--model`/`--provider`/`--preset` or a
   different brief), `--annotation attempt=<k>`.
2. First-wins: `processes wait --spawned-by self user_turn --first --timeout <N>`.
3. Inspect the finisher: if acceptable, `processes stop` the rest and take it; if
   not, keep waiting for the next.
4. Use the winning result.

## Use it when
A task is flaky or latency-sensitive and attempts are independent — diversity raises
the odds one path works.
Not when you need agreement (→ quorum) or every result matters (→ scatter-gather).

## Pitfalls
- "First done" ≠ "correct" — validate the finisher before stopping the others.
- Stop the losers, or they burn cost to no end.
- Identical attempts waste the race — vary something meaningful.

Examples: `examples/hard-bug-race.md`.
