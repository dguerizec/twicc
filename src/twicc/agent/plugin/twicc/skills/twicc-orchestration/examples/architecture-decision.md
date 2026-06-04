# Choose between two architectures

Patterns: `debate` + judge.
Shape: leader = judge → two managers (one per option), each marshalling evidence
with their own workers.

## Walkthrough
1. Frame the choice (option A vs option B) and the criteria that decide it.
2. Spawn two managers, one per option (`--annotation stance=A` / `stance=B`):
   "make the strongest case for <option> against these criteria; gather evidence."
3. Each manager fans out workers to dig sub-points (cost, risk, perf, migration), then
   synthesizes one position and reports it up.
4. Round 2 (optional): send each manager the other's position
   (`send-message <id>` — resurrects it) and ask for a rebuttal.
5. As judge, weigh the two positions against the criteria; decide and explain, or
   synthesize a hybrid.

## Notes
- Assign the stances firmly — a manager told to "stay balanced" produces mush.
- Cap rounds (1–2) up front, or it never converges.
- For extra independence, run the two managers on different providers.
