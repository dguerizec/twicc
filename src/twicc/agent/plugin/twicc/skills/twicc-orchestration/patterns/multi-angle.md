# Multi-angle review

Point N children at the SAME target, each through a different lens, then fuse
their findings.

Shape: star · push or pull · barrier · synthesize · heterogeneous (by angle).

## Who does what
- **Leader or manager** — pick the lenses (e.g. correctness, security, performance,
  style, docs), give each child the same target but a different lens, barrier, then
  fuse into one report (dedup overlaps, keep each lens's unique findings, flag conflicts).
- **Workers** — each examines the whole target from its single assigned lens and
  reports what only that lens sees.

## Protocol
1. Decide the lenses — distinct enough not to overlap, enough to cover the target.
2. Spawn one worker per lens, same target, `--annotation job=<lens>`. For pure
   reading, read-only (`strict`/`dontAsk`) workers are ideal here.
3. Barrier on your direct children.
4. Pull each lens's findings (read-only can't push) and synthesize: group by area,
   drop duplicates, surface conflicts between lenses.

## Use it when
A single artifact must be judged on several independent criteria (a PR, a design,
a document).
Not when the criteria interact and need one combined judgment (→ debate).

## Pitfalls
- Lenses that overlap heavily waste agents — make them distinct.
- This is the canonical home for **read-only analyst** workers; they return text you
  must pull, not push.
- Synthesis is real work — don't just staple the N reports together.

Examples: `examples/pr-review.md`.
