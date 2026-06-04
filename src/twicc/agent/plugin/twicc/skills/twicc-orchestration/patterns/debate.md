# Debate (adversarial + judge)

Have children argue opposing positions, then a judge decides.

Shape: panel · pull + push + steering · rounds · select/synthesize · heterogeneous by stance.

## Who does what
- **Leader** — you are the **judge/arbiter**: assign the stances, run one or more
  rounds, then decide. Each side may be a single child or a **manager** that
  marshals its own evidence (→ multi-level).
- **Manager (a side's lead)** — build the strongest case for your assigned stance:
  fan out workers to gather evidence/sub-arguments, synthesize one position, report it.
- **Workers** — dig one sub-argument or check one fact for their side.

## Protocol
1. Assign opposing stances (for/against, design A/B) — one child or one manager per side.
2. Round 1: each side returns its position (pull it).
3. Optional further rounds: send each side the other's position
   (`send-message <side>` — resurrects it if dead) and ask for a rebuttal.
4. Judge: weigh the positions, decide and explain why; or synthesize a third way.

## Use it when
A consequential either/or where surfacing the strongest case for each side beats a
lone opinion (architecture choices, go/no-go).
Not when you just want a tally (→ quorum).

## Pitfalls
- Stances must be genuinely assigned, not optional — a side that hedges is useless.
- Cap the rounds up front, or the debate never converges.
- The judge stays neutral until the positions are in — don't pre-decide.

Examples: `examples/architecture-decision.md`.
