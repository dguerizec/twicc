# Produce–refute (adversarial verification)

One child produces; another tries to break it. What survives is trustworthy.

Shape: pair (or star of refuters) · push + pull · staged · select · heterogeneous (esp. cross-provider).

## Who does what
- **Leader or manager** — spawn a **producer**, take its output, then spawn one or
  more **refuters** briefed to attack it (find the bug, the counterexample, the hole).
  Keep what survives; send what's refuted back to the producer.
- **Producer worker** — builds the artifact (code, claim, plan).
- **Refuter worker(s)** — actively try to falsify it; report concrete breakages, not
  vague doubts.

## Protocol
1. Producer creates the artifact; collect it.
2. Spawn refuter(s) with the artifact and an explicit "break this" brief.
3. If a refuter finds a real defect, loop: `send-message` the producer with the
   defect (resurrects it) and re-verify the fix.
4. Stop when refuters find nothing, or after a capped number of rounds.

## Use it when
Correctness matters and plausible-but-wrong output is costly (security, money, risky
changes). Strongest as **cross-provider**: producer on one provider, refuter on the
other — each catches what the other rationalizes.
Not when speed matters more than certainty.

## Pitfalls
- A refuter that's too agreeable is worthless — brief it to assume the artifact is wrong.
- Bound the produce↔refute loop so it terminates.
- Refuters report *reproducible* breakages, not opinions.

Examples: `examples/pr-review.md`, `examples/ship-feature.md`, `examples/content-pipeline.md`.
