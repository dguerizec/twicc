# Quorum (independent vote)

Ask the SAME question to N independent children and take the majority answer.

Shape: star · pull or push · barrier · vote · homogeneous or mixed.

## Who does what
- **Leader or manager** — pose one well-defined question to N children that cannot
  see each other, collect their answers, take the majority (or flag a tie /
  no-consensus for a closer look).
- **Workers** — each answers on its own. Independence is the point — never let them confer.

## Protocol
1. Frame a question with a crisp answer space (yes/no, pick-one, a value).
2. Spawn N children with the identical brief; keep them isolated (no sibling chat).
3. Barrier, collect answers, tally.
4. Report the majority and the spread — unanimity vs 3-2 means different confidence.

## Use it when
One judgment is error-prone and you want to damp individual mistakes (a risky call,
an ambiguous classification).
Not when you want reasoning to clash and improve (→ debate), or criteria differ (→ multi-angle).

## Pitfalls
- Odd N avoids ties.
- Identical brief, real isolation — any cross-talk destroys independence.
- Mixing models/providers across the N catches model-specific blind spots; mixing
  prompts measures prompt sensitivity. Choose on purpose.

Examples: `examples/go-no-go-quorum.md`.
