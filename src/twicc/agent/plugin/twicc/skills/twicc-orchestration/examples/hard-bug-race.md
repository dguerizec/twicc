# Crack a hard bug by racing approaches

Patterns: `speculative-race` (with diverse approaches).
Shape: leader → K parallel attempts on the same bug → first acceptable fix wins.

## Walkthrough
1. Spawn K executors on the same bug, each told to try a *different* angle
   (`--annotation attempt=<k>`): e.g. add logging, bisect, rewrite the suspect function,
   check the dependency. Vary model/provider too.
2. First-wins: `processes wait --spawned-by self user_turn --first --timeout <N>`.
3. Inspect the finisher: does its fix actually pass the repro/tests? If yes,
   stop the other attempts by explicit ids, or mark them `status=loser` and run
   `processes stop --spawned-by self --annotation status=loser --timeout <N>`.
   If not, keep waiting for the next.
4. Use the winning fix.

## Notes
- "First done" is not "correct" — validate the finisher (run the repro) before stopping the rest.
- Always stop the losers, or they keep burning cost.
- Diversity is the whole point — identical attempts don't improve the odds.
