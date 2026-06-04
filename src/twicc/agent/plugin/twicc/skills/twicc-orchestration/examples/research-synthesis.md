# Research and synthesize a question

Patterns: `plan-then-execute` + `scatter-gather`.
Shape: leader → (read-only planner) → N read-only researchers → synthesis.

## Walkthrough
1. Spawn a read-only planner: "break this question into independent sub-questions and
   list the sources/angles worth checking." Pull its plan; sanity-check it.
2. Scatter: one read-only researcher per sub-question
   (`--permission-mode dontAsk --annotation job=<sub-question>`), each returning
   findings with citations.
3. Barrier; pull every researcher (read-only can't push).
4. Synthesize: reconcile findings, flag disagreements between sources, write the answer.

## Notes
- Read-only throughout — research reads, it doesn't act.
- A bad plan wastes N researchers; validate it before scattering.
- Bulky source dumps go to the shared scratch, not into messages.
